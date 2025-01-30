from pyearth import Earth
from pyearth._basis import ConstantBasisFunction, LinearBasisFunction, \
    HingeBasisFunction
from typing import Optional, List, Callable
import numpy as np
import scipy.stats
from src.env_model import MARSModel, MARSComponent, ResidualEnvModel, get_environment_model
from e2c.e2c_model import E2CPredictor, fit_e2c
from abstract_interpretation.verification import get_constraints, get_ae_bounds, get_variational_bounds
from abstract_interpretation import domains
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader, TensorDataset
from typing import Union



class MarsE2cModel:
    """
    A model that uses the E2CPredictor to obtain A, B, and c matrices
    and provides a similar interface to MARSModel.
    """
    def __init__(self, e2c_predictor: E2CPredictor, s_dim=None):
        self.e2c_predictor = e2c_predictor
        self.s_dim = s_dim

    def __call__(self, point,  normalized: bool = False) -> np.ndarray:
        """
        Predict the next state given the current state x and action u.
        """
        
        x_norm = point[:self.s_dim]
        u_norm = point[self.s_dim:]
        # Convert to tensors
        x_tensor = torch.tensor(x_norm, dtype=torch.float32).unsqueeze(0)
        u_tensor = torch.tensor(u_norm, dtype=torch.float32).unsqueeze(0)

        # Use E2CPredictor to predict next state
        z_t_next = self.e2c_predictor.get_next_state(x_tensor, u_tensor)

        # Predict next latent state
        

        return z_t_next

    def get_matrix_at_point(self, point: np.ndarray, s_dim: int, steps: int = 1, normalized: bool = False):
        """
        Get the linear model at a particular point.
        Returns M and eps similar to the original MARSModel.
        M is such that the model output can be approximated as M @ [x; 1],
        where x is the input state-action vector.

        Parameters:
        - point: The concatenated (state, action) input vector of length s_dim + u_dim.
        - s_dim: The dimension of the state (and latent dimension, if they match).
        - steps: Number of steps to unroll for error estimation (not used here).
        - normalized: Whether 'point' is already normalized (not used here).

        Returns:
        - M: The linear approximation matrix of shape [s_dim, (s_dim + u_dim + 1)].
        - eps: A vector of length s_dim, taken from diag(A_t @ A_t^T).
        """

        # 1. If needed, unnormalize:
        # if not normalized:
        #     point = (point - self.inp_means) / self.inp_stds

        # 2. Split into state (x_norm) and action (u_norm)
        x_norm = point[:s_dim]
        u_norm = point[s_dim:]

        # 3. Convert to torch tensors
        x_tensor = torch.tensor(x_norm, dtype=torch.float64).unsqueeze(0)
        u_tensor = torch.tensor(u_norm, dtype=torch.float64).unsqueeze(0)

        # 4. Run the E2C transition:
        #    Returns (z_next, z_next_mean, A_t, B_t, c_t, v_t, r_t)
        z_next, z_next_mean, A_t, B_t, c_t, v_t, r_t = self.e2c_predictor.transition(
            x_tensor, x_tensor, u_tensor
        )

        # 5. Convert PyTorch tensors to NumPy, remove batch dimension
        A_t = A_t.detach().cpu().numpy().squeeze(0)    # shape [s_dim, s_dim]
        B_t = B_t.detach().cpu().numpy().squeeze(0)    # shape [s_dim, u_dim]
        c_t = c_t.detach().cpu().numpy().squeeze(0)    # shape [s_dim]

        # 6. Construct M by stacking [A | B | c], giving shape [s_dim, s_dim + u_dim + 1]
        #    Note: c_t[:, None] is the bias column
        M = np.hstack((A_t, B_t, c_t[:, None]))

        # 7. Compute eps as the diagonal of A_t @ A_t^T.
        #    That yields a 1D array of length s_dim.
        A_tA_tT = A_t @ A_t.T  # shape [s_dim, s_dim]
        # eps = np.diag(A_tA_tT) # shape [s_dim]
        eps = np.zeros_like(c_t)

        return M, np.sqrt(eps)



    def __str__(self):
        return "MarsE2cModel using E2CPredictor"

class RewardModel:
    
    def __init__(self, input_size, 
            input_mean: np.ndarray,
            input_std: np.ndarray,
            rew_mean: np.ndarray,
            rew_std: np.ndarray):
        self.model = nn.Sequential(
            nn.Linear(input_size, 8),
            nn.ReLU(),
            nn.Linear(8, 1),
            nn.Sigmoid()
        )
        
        self.input_mean = input_mean
        self.input_std = input_std
        self.rew_mean = rew_mean
        self.rew_std = rew_std
        
    def train(self, X, y):
            
        # Convert inputs and rewards to tensors
        X = torch.Tensor(X)
        rewards = torch.Tensor(y)
        
        # Define the loss function and optimizer
        criterion = nn.MSELoss()
        optimizer = torch.optim.Adam(self.model.parameters(), lr=0.00001)
        
        # Create DataLoader for batching
        dataset = TensorDataset(X, rewards)
        dataloader = DataLoader(dataset, batch_size=32, shuffle=True)
        
        # Training loop
        epochs = 1
        self.model.train()
        for epoch in range(epochs):
            total_loss = 0.0
            for batch_X, batch_rewards in dataloader:
                # Zero the gradients
                optimizer.zero_grad()
                
                # Forward pass
                predictions = self.model(batch_X)
                
                # Compute loss
                loss = criterion(predictions.squeeze(), batch_rewards)
                
                # Backward pass
                loss.backward()
                
                # Update weights
                optimizer.step()
                
                # Accumulate loss
                total_loss += loss.item()
            
            # Print loss for every epoch
            print(f"Epoch {epoch + 1}/{epochs}, Loss: {total_loss / len(dataloader):.4f}")
        
    def __call__(self, X):
        
        X = (X - self.input_mean)/(self.input_std)
        with torch.no_grad():
            rew = self.model(torch.Tensor(X).reshape(1, -1))
        
        return rew.detach().numpy().reshape(-1, ) * (self.rew_std) + self.rew_mean

        

class EnvModel:
    """
    A full environment model including a symbolic model and a neural model.

    This model includes a symbolic (MARS) model of the dynamics, a neural
    model which accounts for dynamics not captured by the symbolic model, and a
    second neural model for the reward function.
    """

    def __init__(
            self,
            mars: MarsE2cModel,
            symb_reward: Union[MARSModel, RewardModel],
            net: ResidualEnvModel,
            reward: ResidualEnvModel,
            use_neural_model: bool,
            observation_space_low,
            observation_space_high):
        """
        Initialize an environment model.

        Parameters:
        mars - A symbolic model.
        net - A neural model for the residuals.
        reward - A neural model for the reward.
        """
        self.mars = mars
        self.symb_reward = symb_reward
        self.net = net
        self.reward = reward
        self.use_neural_model = use_neural_model
        self
        self.observation_space_low = np.array(observation_space_low)
        self.observation_space_high = np.array(observation_space_high)
        

    def __call__(self,
                 state: np.ndarray,
                 action: np.ndarray,
                 use_neural_model: bool = True) -> np.ndarray:
        """
        Predict a new state and reward value for a given state-action pair.

        Parameters:
        state (1D array) - The current state of the system.
        action (1D array) - The action to take

        Returns:
        A tuple consisting of the new state and the reward.
        """
        state = state.reshape(-1, )
        action = action.reshape(-1, )
        inp = np.concatenate((state, action), axis=0)
        symb = self.mars(inp)
        if self.use_neural_model:
            neur = self.net(torch.tensor(inp, dtype=torch.float32)). \
                detach().numpy()
            rew = self.reward(torch.tensor(inp, dtype=torch.float32)).item()
        else:
            neur = np.zeros_like(symb)
            # rew = 0
            rew = self.symb_reward(state)[0]
            
        return np.clip(symb + neur, self.observation_space_low, self.observation_space_high), rew

    def get_symbolic_model(self) -> MARSModel:
        """
        Get the symbolic component of this model.
        """
        return self.mars

    def get_residual_model(self) -> ResidualEnvModel:
        """
        Get the residual neural component of this model.
        """
        return self.net

    def get_confidence(self) -> float:
        return self.confidence

    @property
    def error(self) -> float:
        return self.mars.error




def get_environment_model(     # noqa: C901
        input_states: np.ndarray,
        actions: np.ndarray,
        output_states: np.ndarray,
        rewards: np.ndarray,
        costs: np.ndarray,
        domain,
        seed: int = 0,
        use_neural_model: bool = True,
        arch: Optional[List[int]] = None,
        cost_model: torch.nn.Module = None,
        policy: Optional[Callable[[np.ndarray], np.ndarray]] = None,
        data_stddev: float = 0.01,
        model_pieces: int = 20,
        latent_dim: int = 4,
        horizon: int = 5,
        e2c_predictor = None,
        epochs: int = 50) -> EnvModel:
    """
    Get a neurosymbolic model of the environment.

    This function takes a dataset consisting of M sample input states and
    actions together with the observed output states and rewards. It then
    trains a neurosymbolic model to imitate that data.

    An architecture may also be supplied for the neural parts of the model.
    The architecture format is a list of hidden layer sizes. The networks are
    always fully-connected. The default architecture is [280, 240, 200].

    Parameters:
    input_states (M x S array) - An array of input states.
    actions (M x A array) - An array of actions taken from the input states.
    output_states (M x S array) - The measured output states.
    rewards (M array) - The measured rewards.
    arch: A neural architecture for the residual and reward models.
    """

    
    means = np.mean(input_states, axis=0)
    stds = np.std(input_states, axis=0)
    stds[np.equal(np.round(stds, 2), np.zeros(*stds.shape))] = 1
    
    if e2c_predictor is not None:
        means = e2c_predictor.mean
        stds = e2c_predictor.std
    
    input_states = (input_states - means) / stds
    output_states = (output_states - means) / stds
    
    domain.lower = (domain.lower - means) / stds
    domain.upper = (domain.upper - means) / stds
    print("Input states:", input_states)
    
    if e2c_predictor is None:
        e2c_predictor = E2CPredictor(input_states.shape[-1], latent_dim, actions.shape[-1], horizon = horizon)
    fit_e2c(input_states, actions, output_states, e2c_predictor, e2c_predictor.horizon, epochs=epochs)

    
    lows, highs = get_variational_bounds(e2c_predictor, domain)
    
    lows = lows.detach().numpy()
    highs = highs.detach().numpy()
    
    input_states= input_states.reshape(-1, input_states.shape[-1])
    actions = actions.reshape(-1, actions.shape[-1])
    output_states = output_states.reshape(-1, output_states.shape[-1])
    rewards = rewards.reshape(-1, 1)
    input_states = e2c_predictor.transform(input_states)
    output_states = e2c_predictor.transform(output_states)
    
    e2c_predictor.mean = means
    e2c_predictor.std = stds

    actions_min = actions.min(axis=0)
    actions_max = actions.max(axis=0)
    rewards_min = rewards.min()
    rewards_max = rewards.max()

    print("State stats:", input_states.min(axis = 0), input_states.max(axis = 0))
    print("Action stats:", actions_min, actions_max)
    print("Reward stats:", rewards_min, rewards_max)
    
    
    parsed_mars = MarsE2cModel(e2c_predictor, latent_dim)
    
    X = np.concatenate((input_states, actions), axis=1)
    Yh = np.array([parsed_mars(state, normalized=True) for state in X]).reshape(input_states.shape[0], -1)
    
    print("Model estimation error:", np.mean((Yh - output_states)**2))
    
    
    # Get the maximum distance between a predction and a datapoint
    diff = np.amax(np.abs(Yh - output_states))

    # Get a confidence interval based on the quantile of the chi-squared
    # distribution
    conf = data_stddev * np.sqrt(scipy.stats.chi2.ppf(
        0.9, output_states.shape[1]))
    err = diff + conf
    print("Computed error:", err, "(", diff, conf, ")")
    parsed_mars.error = err

    input_mean, input_std = np.mean(input_states, axis=0), np.std(input_states, axis=0)
    rew_mean, rew_std = np.mean(rewards), np.std(rewards)
    
    input_states = (input_states - input_mean) / (input_std)
    output_states = (output_states - input_mean) / (input_std)
    actions = (actions - actions_min) / (actions_max - actions_min)
    rewards = (rewards - rew_mean) / (rew_std)

    if policy is not None:
        policy_actions = (actions - actions_min) / (actions_max - actions_min)
        next_policy_actions = (actions - actions_min) / (actions_max - actions_min)

    terms = 20
    # Lower penalties allow more model complexity
    # X = np.concatenate((input_states, actions, output_states), axis=1)
    
    X = output_states



    if use_neural_model:
        # Set up a neural network for the residuals.
        state_action = np.concatenate((input_states, actions), axis=1)
        if arch is None:
            arch = [280, 240, 200]
        arch.insert(0, state_action.shape[1])
        arch.append(latent_dim)
        model = ResidualEnvModel(
            arch,
            np.concatenate((lows, highs)),
            np.concatenate((actions_min, actions_max)),
            lows, highs)
        model.train()

        # Set up a training environment
        optim = torch.optim.SGD(model.parameters(), lr=1e-3, momentum=0.9)
        loss = torch.nn.MSELoss()

        data = torch.utils.data.DataLoader(
                torch.utils.data.TensorDataset(
                    torch.tensor(state_action, dtype=torch.float32),
                    torch.tensor(output_states - Yh, dtype=torch.float32)),
                batch_size=128,
                shuffle=True)

        # Train the neural network.
        for epoch in range(100):
            losses = []
            for batch_data, batch_outp in data:
                pred = model(batch_data, normalized=True)
                # Normalize predictions and labels to the range [-1, 1]
                loss_val = loss(pred, batch_outp)
                losses.append(loss_val.item())
                optim.zero_grad()
                loss_val.backward()
                optim.step()
            print("Epoch:", epoch,
                torch.tensor(losses, dtype=torch.float32).mean())

        model.eval()

    
    parsed_rew = RewardModel(X.shape[1], input_mean, input_std, rew_mean, rew_std)
        # np.concatenate((highs, actions_max, highs)),
        # rewards_min[None], rewards_max[None])
    
    parsed_rew.train(X, rewards)

    if use_neural_model:
        # Set up a neural network for the rewards
        arch[-1] = 1
        rew_model = ResidualEnvModel(
            arch,
            np.concatenate((lows, actions_min)),
            np.concatenate((highs, actions_max)),
            rewards_min[None], rewards_max[None])

        optim = torch.optim.Adam(rew_model.parameters(), lr=1e-5)
        loss = torch.nn.SmoothL1Loss()

        # Set up training data for the rewards
        reward_data = torch.utils.data.DataLoader(
                torch.utils.data.TensorDataset(
                    torch.tensor(state_action, dtype=torch.float32),
                    torch.tensor(rewards[:, None], dtype=torch.float32)),
                batch_size=128,
                shuffle=True)

        rew_model.train()

        # Train the network.
        for epoch in range(100):
            losses = []
            for batch_data, batch_outp in reward_data:
                pred = rew_model(batch_data, normalized=True)
                loss_val = loss(pred, batch_outp)
                losses.append(loss_val.item())
                optim.zero_grad()
                loss_val.backward()
                optim.step()
            print("Epoch:", epoch,
                torch.tensor(losses, dtype=torch.float32).mean())

        rew_model.eval()
    else:
        rew_model, model = None, None

    if policy is not None:
        if cost_model is None:
            cost_model = ResidualEnvModel(
                arch,
                np.concatenate((lows, actions_min)),
                np.concatenate((highs, actions_max)),
                0.0, 1.0)

        optim = torch.optim.Adam(cost_model.parameters(), lr=1e-4)
        loss = torch.nn.SmoothL1Loss()

        # Set up training data for the cost_model
        cost_data = torch.utils.data.DataLoader(
                torch.utils.data.TensorDataset(
                    torch.tensor(input_states, dtype=torch.float32),
                    torch.tensor(actions, dtype=torch.float32),
                    torch.tensor(policy_actions, dtype=torch.float32),
                    torch.tensor(next_policy_actions, dtype=torch.float32),
                    torch.tensor(costs[:, None], dtype=torch.float32)),
                batch_size=128,
                shuffle=True)

        cost_model.train()

        # Negative weight overestimates the safety critic rather than
        # underestimating
        q_weight = -1.0
        for epoch in range(1):
            losses = []
            for batch_states, batch_acts, batch_pacts, \
                    batch_npacts, batch_costs in cost_data:
                pred = cost_model(torch.cat((batch_states, batch_acts), dim=1))
                main_loss = loss(pred, batch_costs)
                q_cur = cost_model(torch.cat((batch_states, batch_pacts),
                                             dim=1))
                q_next = cost_model(torch.cat((batch_states, batch_npacts),
                                              dim=1))
                q_cat = torch.cat([q_cur, q_next], dim=1)
                q_loss = torch.logsumexp(q_cat, dim=1).mean() * q_weight
                q_loss = q_loss - pred.mean() * q_weight
                loss_val = main_loss + q_loss
                losses.append(loss_val.item())
                optim.zero_grad()
                loss_val.backward()
                optim.step()
            print("Epoch:", epoch,
                  torch.tensor(losses, dtype=torch.float32).mean())

        cost_model.eval()

    # print(symb.summary())
    print(parsed_mars)
    print("Model MSE:", np.mean(np.sum((Yh - output_states)**2, axis=1)))
    # print(reward_symb.summary())

    return EnvModel(parsed_mars, parsed_rew, model, rew_model,
                    use_neural_model, lows[:input_states.shape[1]], highs[:input_states.shape[1]]), cost_model
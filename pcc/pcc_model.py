import torch
from torch import nn
from pcc.networks import Encoder, Decoder, Dynamics, BackwardDynamics


torch.set_default_dtype(torch.float64)
# torch.manual_seed(0)


class PCC(nn.Module):
    def __init__(self, amortized: bool, x_dim: int, z_dim: int, u_dim: int):
        super(PCC, self).__init__()

        self.x_dim = x_dim
        self.z_dim = z_dim
        self.u_dim = u_dim
        self.amortized = amortized

        self.encoder = Encoder(n_features=x_dim, reduced_dim=z_dim)
        self.decoder = Decoder(reduced_dim=z_dim, n_features=x_dim)
        self.dynamics = Dynamics(z_dim, u_dim, amortized)
        self.backward_dynamics = BackwardDynamics(z_dim, u_dim, x_dim)

    def encode(self, x: torch.Tensor):
        return self.encoder(x)

    def decode(self, z: torch.Tensor):
        return self.decoder(z)

    def transition(self, z: torch.Tensor, u: torch.Tensor):
        return self.dynamics(z, u)

    def back_dynamics(self, z: torch.Tensor, u: torch.Tensor, x: torch.Tensor):
        return self.backward_dynamics(z, u, x)

    def reparam(self, mean: torch.Tensor, std: torch.Tensor):
        # sigma = (logvar / 2).exp()
        epsilon = torch.randn_like(std)
        return mean + torch.mul(epsilon, std)

    def forward(self, x: torch.Tensor, u: torch.Tensor, x_next: torch.Tensor):
        # prediction and consistency loss
        # 1st term and 3rd
        q_z_next = self.encode(x_next)  # Q(z^_t+1 | x_t+1)
        z_next = self.reparam(q_z_next.mean, q_z_next.stddev)  # sample z^_t+1
        p_x_next = self.decode(z_next)  # P(x_t+1 | z^t_t+1)
        # 2nd term
        q_z_backward = self.back_dynamics(z_next, u, x)  # Q(z_t | z^_t+1, u_t, x_t)
        p_z = self.encode(x)  # P(z_t | x_t)

        # 4th term
        z_q = self.reparam(q_z_backward.mean, q_z_backward.stddev)  # samples from Q(z_t | z^_t+1, u_t, x_t)
        p_z_next, _, _ = self.transition(z_q, u)  # P(z^_t+1 | z_t, u _t)

        # additional VAE loss
        z_p = self.reparam(p_z_next.mean, p_z_next.stddev)  # samples from P(z_t | x_t)
        p_x = self.decode(z_p)  # for additional vae loss

        # additional deterministic loss
        mu_z_next_determ = self.transition(p_z.mean, u)[0].mean
        p_x_next_determ = self.decode(mu_z_next_determ)

        return p_x_next, q_z_backward, p_z, q_z_next, z_next, p_z_next, z_p, u, p_x, p_x_next_determ

    @torch.no_grad()
    def predict(self, x: torch.Tensor, u: torch.Tensor):

        x = torch.tensor(x, dtype=torch.float64)
        u = torch.tensor(u, dtype=torch.float64)
        z_dist = self.encoder(x)
        z = z_dist.mean
        x_recon = self.decode(z)

        transition_dist, A, B = self.transition(z, u)
        # z_next = self.reparam(mu_next, logvar_next)
        x_next_pred = self.decode(transition_dist.mean)
        return x_recon, x_next_pred
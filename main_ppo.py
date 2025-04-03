import argparse
import datetime
import itertools
import numpy as np
import torch
import os
from torch.utils.tensorboard import SummaryWriter
from ppo import PPO, ActorCritic
from src.policy import Shield, PPOPolicy, ProjectionPolicy
from e2c.env_model import get_environment_model
from abstract_interpretation import domains, verification
from benchmarks import envs
from pytorch_soft_actor_critic.replay_memory import ReplayMemory
import gymnasium as gym 

import traceback

parser = argparse.ArgumentParser(description='Safe PPO Args')
parser.add_argument('--env_name', default="lunar_lander")
parser.add_argument('--gamma', type=float, default=0.99)
parser.add_argument('--lr', type=float, default=0.0001)
parser.add_argument('--seed', type=int, default=123456)
parser.add_argument('--batch_size', type=int, default=2048)
parser.add_argument('--mini_batch_size', type=int, default=64)
parser.add_argument('--num_steps', type=int, default=200000)
parser.add_argument('--hidden_size', type=int, default=64)
parser.add_argument('--replay_size', type=int, default=1000000)
parser.add_argument('--start_steps', type=int, default=3000)
parser.add_argument('--cuda', action="store_true")
parser.add_argument('--horizon', type=int, default=20)
parser.add_argument('--red_dim', type=int, default = 20)
parser.add_argument('--no_safety', default=False, action='store_true')
args = parser.parse_args()

# Setup environment
env = envs.get_env_from_name(args.env_name)
env.seed(args.seed)
torch.manual_seed(args.seed)
np.random.seed(args.seed)

hyperparams = vars(args)

# Tensorboard
if not os.path.exists("runs_ppo"):
    os.makedirs("runs_ppo")
writer = SummaryWriter('runs_ppo/{}_PPO_{}_H{}_D{}'.format(
    datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S"), args.env_name,
    args.horizon, args.red_dim))

print(hyperparams)
if not os.path.exists("logs_ppo"):
    os.makedirs("logs_ppo")

file = open('logs_ppo/{}_PPO_{}_H{}_D{}.txt'.format(
    datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S"), args.env_name,
    args.horizon, args.red_dim), "w+")

# PPO agent setup

# Replay memories
real_data = ReplayMemory(args.replay_size, env.observation_space, env.action_space.shape[0], args.seed)
e2c_data = ReplayMemory(args.replay_size, env.observation_space, env.action_space.shape[0], args.seed)


iterator_loop = itertools.count(1)

real_unsafe_episodes = 0
total_real_episodes = 0
total_numsteps = 0
agent = PPOPolicy(env, args.batch_size, args.seed, args.batch_size, args)
safe_agent = None

# Training loop

env_model = None
unsafe_test_episodes = 0
total_test_episodes = 0
unsafe_sim_episodes = 0
total_sim_episodes = 0
while True:
    i_episode = next(iterator_loop)
    episode_reward = 0
    episode_steps = 0
    done = False
    trunc = False
    while True:
        state, info = env.reset()
        if not env.unsafe(state, False):
            break

    if True:

        print(i_episode, ": Real data")
        tmp_buffer = []
        real_buffer = []
        
        while not done and not trunc:
            if safe_agent is not None:
                action = safe_agent(state)
            else:
                action = agent(state)

            next_state, reward, done, trunc, info = env.step(action)
            episode_steps += 1
            total_numsteps += 1
            episode_reward += reward

            cost = 0
            if env.unsafe(next_state, False):
                real_unsafe_episodes += 1
                episode_reward -= 100
                reward = -100
                print("UNSAFE (outside testing)")
                print(f"{np.round(state, 2)}", "\n", action, "\n", f"{np.round(next_state, 2)}")
                done = True
                cost = 1

            # Ignore the "done" signal if it comes from hitting the time
            # horizon.
            # github.com/openai/spinningup/blob/master/spinup/algos/sac/sac.py

            if cost > 0:
                agent.add(state, action, reward, next_state, done or trunc, 1)
                real_data.push(state, action, reward, next_state, done or trunc, 1)
            else:
                agent.add(state, action, reward, next_state, done or trunc, 0)
                real_data.push(state, action, reward, next_state, done or trunc, 0)
            
            
            if len(agent.memory) >= args.batch_size:
                losses = agent.train()

                writer.add_scalar(f'loss/policy_loss', losses['avg_policy_loss'], total_numsteps)
                writer.add_scalar(f'loss/value_loss', losses['avg_value_loss'], total_numsteps)
                writer.add_scalar(f'loss/entropy_loss', losses['avg_entropy_loss'], total_numsteps)
                writer.add_scalar(f'loss/clip_fraction', losses['avg_clip_fraction'], total_numsteps)
                writer.add_scalar(f'loss/kl_div', losses['avg_kl_divergence'], total_numsteps)
                writer.add_scalar(f'loss/total_loss', losses['avg_total_loss'], total_numsteps)
                writer.add_scalar(f'loss/explained_variance', losses['avg_explained_variance'], total_numsteps)


            state = next_state

            
        
        if safe_agent is not None:
            try:
                s, a, b, t = safe_agent.report()
                print("Shield steps:", s, "  Neural steps:", a, "  Backup steps:", b)
                print("Average time:", t / (s + a + b))
                safe_agent.reset_count()
            except Exception:
                pass
        
        total_real_episodes += 1 

    # elif env_model is not None:
        
    #     print(i_episode, ": Simulated data")

    #     while not done:
    #         if episode_steps % 100 == 0:
    #             print(i_episode, episode_steps, total_numsteps)
            
    #         action = agent(state)  # Sample action from policy

    #         next_state, reward = env_model(state, action,
    #                                        use_neural_model=False)
            
    #         true_next_state, _, _, _, _ = env.step(action)
            
    #         done = not np.all(np.abs(next_state) < 1e5) and \
    #             not np.any(np.isnan(next_state))
    #         # done = done or env.pred`ict_done(next_state)
    #         done = done or episode_steps == env._max_episode_steps or \
    #             not np.all(np.abs(next_state) < 1e5)
    #         episode_steps += 1
    #         total_numsteps += 1
    #         episode_reward += reward

    #         cost = 0
    #         # print("Next state:", np.round(next_state, 2), "True next state:", np.round(true_next_state, 2))
    #         if env.unsafe(next_state, False):
    #             print("UNSAFE SIM", next_state)
    #             unsafe_sim_episodes += 1
    #             reward =-10
    #             episode_reward -= 10
    #             done = True
    #             cost = 1

    #         # Ignore the "done" signal if it comes from hitting the time
    #         # horizon.
    #         # github.com/openai/spinningup/blob/master/spinup/algos/sac/sac.py

    #         agent.add(state.reshape(-1, ), action.reshape(-1, ), reward, next_state.reshape(-1, ), done, cost)
                    
                        
    #         if len(agent.memory) >= args.batch_size:
    #             losses = agent.train()

    #             writer.add_scalar(f'loss/policy_loss', losses['avg_policy_loss'], total_numsteps)
    #             writer.add_scalar(f'loss/value_loss', losses['avg_value_loss'], total_numsteps)
    #             writer.add_scalar(f'loss/entropy_loss', losses['avg_entropy_loss'], total_numsteps)
    #             writer.add_scalar(f'loss/clip_fraction', losses['avg_clip_fraction'], total_numsteps)
    #             writer.add_scalar(f'loss/kl_div', losses['avg_kl_divergence'], total_numsteps)
    #             writer.add_scalar(f'loss/total_loss', losses['avg_total_loss'], total_numsteps)
    #             writer.add_scalar(f'loss/explained_variance', losses['avg_explained_variance'], total_numsteps)



    #         # state =  env_model.mars.e2c_predictor.inverse_transform(next_state)
    #         state =  next_state
        
    #     total_sim_episodes += 1 



    if ((i_episode-1) // 10) % 10 == 0 and ((i_episode) // 10) % 10 != 0 and not args.no_safety:
    # if False:
        try:
            
            print("E2C DATA", len(e2c_data))
            states, actions, rewards, next_states, dones, costs = \
                real_data.sample(min(len(real_data), 70000), get_cost=True, remove_samples = True)
                
            states_e2c, actions_e2c, rewards_e2c, next_states_e2c, dones_e2c, costs_e2c = \
                e2c_data.sample(min(len(e2c_data), 30000), get_cost=True, remove_samples = False)
                
            for idx in range(states.shape[0]):
                state = states[idx]
                action = actions[idx]
                reward = rewards[idx]
                next_state = next_states[idx]
                mask = dones[idx]
                cost = costs[idx]
                e2c_data.push(state, action, reward, next_state, mask, cost)

            
            states = np.vstack([states, states_e2c])
            actions = np.vstack([actions, actions_e2c])
            rewards = np.concatenate([rewards, rewards_e2c])
            next_states = np.vstack([next_states, next_states_e2c])
            dones = np.concatenate([dones, dones_e2c])
            costs = np.concatenate([costs, costs_e2c])
            
        except Exception as e:
            
            print(traceback.format_exc())
            print("Error in sampling")
            continue
        
        if env_model is not None:
            env_model.mars.e2c_predictor.lr = 0.00003
            e2c_predictor = env_model.mars.e2c_predictor
            epochs = 40
        else:
            e2c_predictor = None
            epochs = 150
    
        env_model = get_environment_model(
                states, actions, next_states, rewards,
                domains.DeepPoly(env.original_observation_space.low, env.original_observation_space.high),
                seed=args.seed, e2c_predictor = e2c_predictor, latent_dim=args.red_dim, horizon = args.horizon, epochs= epochs)
        
            
            
        e2c_mean = env_model.mars.e2c_predictor.mean
        e2c_std = env_model.mars.e2c_predictor.std
        new_obs_space_domain = domains.DeepPoly(*verification.get_ae_bounds(env_model.mars.e2c_predictor, domains.DeepPoly((env.original_observation_space.low - e2c_mean)/e2c_std, (env.original_observation_space.high - e2c_mean)/e2c_std)))
        new_obs_space = gym.spaces.Box(low=new_obs_space_domain.lower[0].detach().numpy(), high=new_obs_space_domain.upper[0].detach().numpy(), shape=(args.red_dim,))
        
        safety_domain = domains.DeepPoly((env.original_safety.lower - e2c_mean)/e2c_std, (env.original_safety.upper - e2c_mean)/e2c_std)
        
        safety = domains.DeepPoly(*verification.get_ae_bounds(env_model.mars.e2c_predictor, safety_domain))
        
        
        
        unsafe_domains_list = domains.recover_safe_region(new_obs_space_domain, [safety])
            
        
        polys = safety.to_hyperplanes()

        env.transformed_safe_polys = polys
        # env.state_processor = env_model.mars.e2c_predictor.transform
        env.transformed_polys = [domain.to_hyperplanes() for domain in unsafe_domains_list]

        print("LATENT SAFETY", safety)
        print("LATENT OBS SPACE", new_obs_space_domain)

        shield = ProjectionPolicy(
            env_model.get_symbolic_model(), new_obs_space,
            env.action_space, args.horizon, env.transformed_polys, env.transformed_safe_polys, env_model.mars.e2c_predictor.transform)
        safe_agent = Shield(shield, agent)
        
        

    # Test the agent periodically
    
    writer.add_scalar(f'reward/train', episode_reward, total_numsteps)
    print("Episode: {}, total numsteps: {}, episode steps: {}, reward: {}"
          .format(i_episode, total_numsteps,
                  episode_steps, round(episode_reward, 2)))
    if safe_agent is not None:
        safe_agent.reset_count()

    if (i_episode - 99) % 1 == 0:
        print("starting testing...")
        avg_reward = 0.
        episodes = 1
        unsafe_episodes = 0
        avg_length = 0.
        shield_count = 0
        backup_count = 0
        neural_count = 0
        t = 0

        for episode_num in range(episodes):
            # record_video = episode_num % 2 == 0  # Record every alternate episode (example condition)
            custom_filename = f"videos/episode_{i_episode}.mp4"
            
            # video_env.video_recorder.file_prefix = os.path.join("videos/", f"{custom_filename.split('.')[0]}")
            
            while True:
                state, info = env.reset()
                if not env.unsafe(state, False):
                    break
            episode_reward = 0
            done = False
            trunc = False
            episode_steps = 0
            trajectory = [state]
            # frames  = [env.render()]

            while not done and not trunc:
                # Decide action
                if safe_agent is not None:
                    action = safe_agent(state)
                else:
                    action = agent(state)

                next_state, reward, done, trunc, info = env.step(action)
                episode_reward += reward
                episode_steps += 1

                if episode_steps >= env._max_episode_steps:
                    done = True
                if env.unsafe(next_state, False):
                    print("UNSAFE Inside testing")
                    episode_reward += -100
                    print(f"{np.round(state, 2)}", "\n", action, "\n", f"{np.round(next_state, 2)}")
                    unsafe_episodes += 1
                    done = True

                if done and safe_agent is not None:
                    try:
                        s, a, b, t = safe_agent.report()
                        print("Finished test episode:", s, "shield and", b, "backup and", a, "neural")
                        shield_count += s
                        backup_count += b
                        neural_count += a

                        print("Average time:", t / (s + a + b))
                        safe_agent.reset_count()
                    except Exception as e:
                        print(e)
                        pass

                state = next_state
                trajectory.append(state)
                # frames.append(env.render())

            # imageio.mimsave(custom_filename, frames, fps=30)
            avg_reward += episode_reward
            avg_length += episode_steps

            avg_reward /= episodes
            avg_length /= episodes
            shield_count /= episodes
            neural_count /= episodes
            backup_count /= episodes
            unsafe_test_episodes+=unsafe_episodes
            total_test_episodes+=episodes
            writer.add_scalar(f'agent/shield', shield_count, total_numsteps)
            writer.add_scalar(f'agent/neural', neural_count, total_numsteps)
            writer.add_scalar(f'agent/backup', backup_count, total_numsteps)
            writer.add_scalar(f'agent/unsafe_real_episodes', real_unsafe_episodes, total_numsteps)
            writer.add_scalar(f'agent/unsafe_real_episodes_ratio', real_unsafe_episodes/total_real_episodes, total_numsteps)
            writer.add_scalar(f'agent/unsafe_sim_episodes', unsafe_sim_episodes, total_numsteps)
            writer.add_scalar(f'agent/unsafe_sim_episodes_ratio', (unsafe_sim_episodes+0.0000001)/(total_sim_episodes+0.0000001), total_numsteps)
            writer.add_scalar(f'agent/unsafe_test_episodes', unsafe_test_episodes, total_numsteps)
            writer.add_scalar(f'agent/unsafe_test_episodes_ratio', (unsafe_test_episodes+0.0000001)/(total_test_episodes + 0.0000001), total_numsteps)
            writer.add_scalar(f'reward/test', avg_reward, total_numsteps)

            print("----------------------------------------")
            print("Test Episodes: {}, Unsafe: {}, Avg. Length: {}, Avg. Reward: {}"
                .format(episodes, unsafe_episodes, round(avg_length, 2),
                        round(avg_reward, 2)))
            print("----------------------------------------")
            if (i_episode - 99) % 100 == 0:
                print("Trajectory:")
                print(trajectory)    
            # total_episodes += 1 
        
        
    if total_numsteps > args.num_steps:
        break
    
total_episodes = next(iterator_loop) - 1
print("Total unsafe real:", real_unsafe_episodes, "/", total_real_episodes)
print("Total unsafe real:", real_unsafe_episodes, "/", total_real_episodes, file=file)
print("Total unsafe sim:", unsafe_sim_episodes, "/", total_sim_episodes)
print("Total unsafe sim:", unsafe_sim_episodes, "/", total_sim_episodes, file=file)
print("Total unsafe Test:", unsafe_test_episodes, "/", total_test_episodes)
print("Total unsafe Test:", unsafe_test_episodes, "/", total_test_episodes, file=file)
print("Using SPICE:", not args.no_safety)
print("Using SPICE:", not args.no_safety, file=file)


writer.add_hparams(
    hparam_dict = hyperparams, 
    metric_dict = {
        "Unsafe Real Episodes": real_unsafe_episodes, 
        "Unsafe Sim Episodes": unsafe_sim_episodes, 
        "Unsafe Test Episodes": unsafe_test_episodes,
        "Total Real Episodes": total_real_episodes, 
        "Total Sim Episodes": total_sim_episodes, 
        "Total Test Episodes":total_test_episodes
    }
)
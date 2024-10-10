import random
import numpy as np
import pickle
import os

class ReplayMemory:
    def __init__(self, capacity, observation_space, seed):
        random.seed(seed)
        self.capacity = capacity
        self.buffer = []
        self.position = 0
        self.observation_space = observation_space

    def push(self, state, action, reward, next_state, done, cost):

        # optional for adding noise
        noise_level = np.random.uniform(0.2, 0.4)
        # state = state + noise_level * np.random.randn(*state.shape)
        # next_state = next_state + noise_level * np.random.randn(*next_state.shape)

        # state = np.clip(state, self.observation_space.low, self.observation_space.high)
        # state = np.clip(next_state, self.observation_space.low, self.observation_space.high)
        
        if len(self.buffer) < self.capacity:
            self.buffer.append(None)
        self.buffer[self.position] = (state, action, reward, next_state, done, cost)
        self.position = (self.position + 1) % self.capacity

    def sample(self, batch_size, get_cost=False):

        batch = random.sample(self.buffer, batch_size)

        state, action, reward, next_state, done, cost =  map(np.stack, zip(*batch))
        if get_cost:
            return state, action, reward, next_state, done, cost
        return state, action, reward, next_state, done

    def __len__(self):
        return len(self.buffer)

    def save_buffer(self, env_name, suffix="", save_path=None):
        if not os.path.exists('checkpoints/'):
            os.makedirs('checkpoints/')

        if save_path is None:
            save_path = "checkpoints/sac_buffer_{}_{}".format(env_name, suffix)
        print('Saving buffer to {}'.format(save_path))

        with open(save_path, 'wb') as f:
            pickle.dump(self.buffer, f)

    def load_buffer(self, save_path):
        print('Loading buffer from {}'.format(save_path))

        with open(save_path, "rb") as f:
            self.buffer = pickle.load(f)
            self.position = len(self.buffer) % self.capacity

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
        if len(self.buffer) < self.capacity:
            self.buffer.append(None)
        self.buffer[self.position] = (state, action, reward, next_state, done, cost)
        self.position = (self.position + 1) % self.capacity

    def sample(self, batch_size, get_cost=False, remove_samples=False):
        # Get random sample indices
        sample_indices = random.sample(range(len(self.buffer)), batch_size)

        # Retrieve the sampled elements
        batch = [self.buffer[i] for i in sample_indices]
        state, action, reward, next_state, done, cost = map(np.stack, zip(*batch))

        # Remove the sampled elements if requested
        if remove_samples:
            # Create a new buffer excluding the sampled elements
            new_buffer = [self.buffer[i] for i in range(len(self.buffer)) if i not in sample_indices]
            self.buffer = new_buffer
            self.position = len(self.buffer) % self.capacity

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

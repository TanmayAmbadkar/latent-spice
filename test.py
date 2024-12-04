# import gymnasium as gym
import numpy as np
import matplotlib.pyplot as plt
import tqdm
import safety_gymnasium
import imageio
import gymnasium as gym

# Define the Double Inverted Pendulum environment
env = gym.make("BipedalWalker-v3", render_mode = "rgb_array")
print("OBS SPACE", env.observation_space.shape)
# Number of episodes to run
num_episodes = 5

# List to collect unsafe states
unsafe_states = []
safe_states = []
states = []
average_length = []
# Run the simulation
for episode in tqdm.tqdm(range(num_episodes)):
    state, info = env.reset()
    done = False
    trunc = False
    temp_states = []
    temp_states.append(state)
    images = []
    while not done and not trunc:
        # Take a random action
        action = env.action_space.sample()
        next_state, reward, done, trunc, info = env.step(action)
        state = next_state
        
        temp_states.append(state)
        images.append(env.render())
        
    imageio.mimsave(f"{episode}.gif", images, duration=1)
    average_length.append(len(temp_states))
    unsafe_states += temp_states[int(len(temp_states)*0.7):]
    safe_states += temp_states[:int(len(temp_states)*0.7)]
    states += temp_states
    
print("Unsafe States stats")
print("MIN:", np.round(np.min(unsafe_states, axis = 0), 2)[:12])
# print("CENTER:", np.round(np.mean(unsafe_states, axis = 0), 2))
print("MAX:", np.round(np.max(unsafe_states, axis = 0), 2)[:12])
    
print("Safe States stats")
print("MIN:", np.round(np.min(safe_states, axis = 0), 2)[:12])
# print("CENTER:", np.round(np.mean(unsafe_states, axis = 0), 2))
print("MAX:", np.round(np.max(safe_states, axis = 0), 2)[:12])
    
print("All States stats")
print("MIN:", np.round(np.min(states, axis = 0), 2))
# print("CENTER:", np.round(np.mean(states, axis = 0)))
print("MAX:", np.round(np.max(states, axis = 0), 2))

plt.figure(figsize=(10, env.observation_space.shape[0] + 10))

unsafe_states = np.array(unsafe_states)
safe_states = np.array(safe_states)
states = np.array(states)

for i in range(12):
    plt.subplot(12, 1, i+1)
    
    # Plot for unsafe states
    plt.plot([np.round(np.min(unsafe_states, axis=0), 2)[i], np.round(np.max(unsafe_states, axis=0), 2)[i]],
             [1, 1], label="Unsafe States", color='r')
    plt.scatter(x = unsafe_states[:, i], y = np.ones(len(unsafe_states)), color = "r")
    
    # Plot for all states
    plt.plot([np.round(np.min(states, axis=0), 2)[i], np.round(np.max(states, axis=0), 2)[i]],
             [0, 0], label="All States", color='b')
    plt.scatter(x = states[:, i], y = np.zeros(len(states)), color = "b")
    
    # Plot for safe states
    plt.plot([np.round(np.min(safe_states, axis=0), 2)[i], np.round(np.max(safe_states, axis=0), 2)[i]],
             [-1, -1], label="Safe States", color='g')
    plt.scatter(x = safe_states[:, i], y = -np.ones(len(safe_states)), color = "g")

plt.legend()

plt.savefig("out.png")

print("Average Length", np.mean(average_length), np.std(average_length))

import torch
import torch.nn.functional as F
from pcc.networks import MultivariateNormalDiag
from torch.distributions.kl import kl_divergence



def bernoulli(x, p):
    p = p.probs
    log_p_x = torch.sum(x * torch.log(1e-15 + p) + (1 - x) * torch.log(1e-15 + 1 - p), dim=-1)
    log_p_x = torch.mean(log_p_x)
    return log_p_x


def KL(normal_1, normal_2):
    kl = kl_divergence(normal_1, normal_2)
    kl = torch.mean(kl)
    return kl


def entropy(p):
    H = p.entropy()
    H = torch.mean(H)
    return H


def gaussian(z, p):
    log_p_z = p.log_prob(z)
    log_p_z = torch.mean(log_p_z)
    return log_p_z


def vae_bound(x, dist_p_x, p_z):
    recon_loss = -gaussian(x, dist_p_x)
    regularization_loss = KL(p_z, MultivariateNormalDiag(torch.zeros_like(p_z.mean), torch.ones_like(p_z.stddev)))
    return recon_loss + regularization_loss


def ae_loss(x, p_x):
    recon_loss = F.mse_loss(x, p_x)
    return recon_loss


def curvature(model, z, u, delta, armotized):
    z_alias = z.detach().requires_grad_(True)
    u_alias = u.detach().requires_grad_(True)
    eps_z = torch.normal(mean=torch.zeros_like(z), std=torch.empty_like(z).fill_(delta))
    eps_u = torch.normal(mean=torch.zeros_like(u), std=torch.empty_like(u).fill_(delta))
    # print ('eps u ' + str(eps_u.size()))
    z_bar = z_alias + eps_z
    u_bar = u_alias + eps_u

    f_z_bar, A_bar, B_bar = model.transition(z_bar, u_bar)
    f_z_bar = f_z_bar.mean
    f_z, A, B = model.transition(z_alias, u_alias)
    f_z = f_z.mean
    # print ('f_z ' + str(f_z.size()))
    if not armotized:
        grad_z, grad_u = torch.autograd.grad(f_z, [z_alias, u_alias], grad_outputs=[eps_z, eps_u], retain_graph=True, create_graph=True)
        taylor_error = f_z_bar - (grad_z + grad_u) - f_z
        cur_loss = torch.mean(torch.sum(taylor_error.pow(2), dim = 1))
    else:
        z_dim, u_dim = z.size(1), u.size(1)
        A_bar = A_bar.view(-1, z_dim, z_dim)
        B_bar = B_bar.view(-1, z_dim, u_dim)
        eps_z = eps_z.view(-1, z_dim, 1)
        eps_u = eps_u.view(-1, u_dim, 1)
        taylor_error = f_z_bar - (torch.bmm(A_bar, eps_z).squeeze() + torch.bmm(B_bar, eps_u).squeeze()) - f_z
        cur_loss = torch.mean(torch.sum(taylor_error.pow(2), dim = 1))
    return cur_loss


def get_jacobian(dynamics, batched_z, batched_u):
    """
    compute the jacobian of F(z,u) w.r.t z, u
    """
    batch_size = batched_z.size(0)
    z_dim = batched_z.size(-1)
    # u_dim = batched_u.size(-1)

    z, u = batched_z.unsqueeze(1), batched_u.unsqueeze(1)  # batch_size, 1, input_dim
    z, u = z.repeat(1, z_dim, 1), u.repeat(1, z_dim, 1)  # batch_size, output_dim, input_dim
    z_next = dynamics(z, u)[0].mean
    grad_inp = torch.eye(z_dim).reshape(1, z_dim, z_dim).repeat(batch_size, 1, 1).cuda()
    all_A, all_B = torch.autograd.grad(z_next, [z, u], [grad_inp, grad_inp], create_graph=True, retain_graph=True)
    return all_A, all_B

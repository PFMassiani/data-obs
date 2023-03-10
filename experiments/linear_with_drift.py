from pathlib import Path
from sklearn.preprocessing import StandardScaler
import numpy as np
import time
import sys

sys.path.append('.')
from experiments.drift_utils import dump_specs, get_new_experiment_folder, \
    log_test, log_initial_state_data, log_mmds
import dynamical_systems.dynamical_systems as ds
from kernels_dynamical_systems.custom_kernels import TrajectoryRBFTwoSampleTest


def generate_trajs_from_initial_state(system, T, dt, initial_state, N_traj,
                                      *args, **kwargs):
    prev_initializer = system.state_initializer
    initializer = ds.ConstantInitializer(initial_state)
    system.state_initializer = initializer
    trajs, t = system.get_trajectories(N_traj, T, dt, *args, **kwargs)
    meas = system.get_output_trajectories(N_traj, T, traj=trajs)
    system.state_initializer = prev_initializer
    return trajs, meas, t


def get_mmd_map(system, initial_state, others, N_traj_initial_state,
                N_traj_others, T, dt, alpha, experiment_folder, sigma=None):
    trajs_initial_state, meas_initial_state, t = \
        generate_trajs_from_initial_state(
            system, T, dt, initial_state, N_traj_initial_state
        )
    log_initial_state_data(
        experiment_folder, trajs_initial_state, meas_initial_state)
    mmds = np.zeros(others.shape[0], dtype=float)
    rejected = np.zeros(others.shape[0], dtype=bool)
    thresholds = np.zeros(others.shape[0], dtype=float)
    test_numbers_allocation = np.zeros(others.shape[0])
    if sigma is None:
        # Save map of sigma values since not fixed
        sigmas = np.zeros_like(mmds)
    for n_other, other in enumerate(others):
        if ((n_other + 1) % 50) == 0:
            print(f'Simulation {n_other + 1}/{others.shape[0]}')
        t1 = time.time()
        trajs_other, meas_other, _ = generate_trajs_from_initial_state(
            system,
            T,
            dt,
            other,
            N_traj_others
        )
        scaler = StandardScaler()  # scaler over (2 * T * N, dim_obs)
        dim_obs = meas_initial_state.shape[-1]
        whole_meas = np.transpose(np.concatenate(
            (meas_initial_state, meas_other), axis=1), (1, 0, 2)).reshape(-1, dim_obs)
        scaler = scaler.fit(whole_meas)
        scaled_meas1 = scaler.transform(
            meas_initial_state.reshape(-1, dim_obs)).reshape(N_traj_initial_state, -1, dim_obs)
        scaled_meas2 = scaler.transform(
            meas_other.reshape(-1, dim_obs)).reshape(N_traj_others, -1, dim_obs)
        test = TrajectoryRBFTwoSampleTest(
            # meas_initial_state, meas_other,
            scaled_meas1, scaled_meas2,
            alpha=alpha,
            sigma=sigma  # TODO should be fixed for whole grid!
        )
        test.perform_test()
        rejected[n_other] = not test.is_null_hypothesis_accepted()
        mmds[n_other] = test.test_stat
        thresholds[n_other] = test.threshold
        t2 = time.time()
        test_numbers_allocation[n_other] = log_test(
            experiment_folder, other, trajs_other, meas_other, test, t2 - t1
        )  # logging
        if sigma is None:
            sigmas[n_other] = test.sigma
    results = {'t': t, 'mmds': mmds, 'rejected': rejected, 'thresholds':
        thresholds, 'test_numbers_allocation': test_numbers_allocation}
    if sigma is None:
        results.update({'sigmas': sigmas})
    return results


if __name__ == '__main__':
    ROOT = Path(__file__).parent.parent
    RESULTS = ROOT / 'Results'
    DRIFT = RESULTS / 'Drift'

    seed_np = 0
    seed_rng = 42

    # Logging
    experiment_folder = get_new_experiment_folder(DRIFT)
    print(f'Saving results to {experiment_folder}')

    # System Definition
    dim = 2
    meas_dim = 1
    pulse = 2.
    phase = 0.
    amplitude = 3
    process_noise_std = 0.1 * np.eye(dim)
    meas_noise_var = 1e-2 * np.eye(meas_dim)
    A = np.array([
        [-2, -1.],
        [-1., -2.]
    ])  # eigenvalues -1 and -3, eigenvectors [-1, 1] and [1, 1]
    B = np.eye(dim)
    C = np.array([-1., 1.])
    initializer = ds.ConstantInitializer([0., 0.])
    controller = ds.SinusoidalController(
        dim=dim, pulse=pulse, phase=phase, amplitude=amplitude)
    drift_noise = ds.LinearBrownianMotionNoise(sigma=process_noise_std)
    measurement = ds.LinearMeasurement(C)
    meas_noise = ds.GaussianNoise(0., meas_noise_var)
    system = ds.ContinuousTimeLTI(
        dim=2,
        A=A,
        B=B,
        state_initializer=initializer,
        controller=controller,
        noise=drift_noise,
        meas=measurement,
        meas_noise=meas_noise
    )

    # Test
    initial_state = np.array([1.5, 0.5])
    N_grid = 50
    grid = np.linspace(-2, 2, N_grid)
    others = np.dstack(np.meshgrid(grid, grid, indexing='xy')).reshape(-1, dim)
    N_traj_initial_state = 30
    N_traj_others = 30
    T = 2
    dt = 1e-2
    alpha = 0.05
    sigma = 5

    dump_specs(experiment_folder, locals().copy())  # logging

    mmd_map_results = get_mmd_map(
        system,
        initial_state=initial_state,
        others=others,
        N_traj_initial_state=N_traj_initial_state,
        N_traj_others=N_traj_others,
        T=T,
        dt=dt,
        alpha=alpha,
        experiment_folder=experiment_folder,
        sigma=sigma
    )
    t = mmd_map_results['t']
    mmds = mmd_map_results['mmds'].reshape(N_grid, N_grid)
    rejected = mmd_map_results['rejected'].reshape(N_grid, N_grid)
    thresholds = mmd_map_results['thresholds'].reshape(N_grid, N_grid)
    test_numbers_allocation = mmd_map_results['test_numbers_allocation']
    if sigma is None:
        # Save map of sigma values
        sigmas = mmd_map_results['sigmas'].reshape(N_grid, N_grid)
        sigmas_file = experiment_folder / 'sigmas.npy'
        with sigmas_file.open('wb') as f:
            np.save(file=f, arr=sigmas, allow_pickle=False)

    xinf = grid[0]
    xsup = grid[-1]
    log_mmds(experiment_folder, t, mmds, rejected, thresholds,
             test_numbers_allocation, others,
             extent=(xinf, xsup, xinf, xsup))

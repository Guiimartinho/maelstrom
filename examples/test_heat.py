#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
from __future__ import print_function

from dolfin import (
    plot, interactive, FacetNormal, dot, grad, pi, dx, ds, Constant, Measure,
    XDMFFile, solve, Function, DirichletBC, TrialFunction, TestFunction, lhs,
    rhs, SpatialCoordinate
    )

import problems

import maelstrom.time_steppers as ts

import pytest


def _main():
    # # for boundary conditions
    # heat_transfer_coefficient = {'upper': 50.0,
    #                              'upper left': 300.0,
    #                              'crucible': 15.0}
    # T = {'upper': 1480.0,
    #      'upper left': 1500.0,
    #      'crucible': 1660.0}
    test_solve(
        # heat_transfer_coefficient, T,
        stationary=False
        )
    return


def _parameter_quest():
    '''Find parameter sets fitting crucible data.
    '''
    import numpy as np

    # Create the set of parameter values to search.
    flux0 = [500.0*k for k in range(11)]
    flux1 = [500.0*k for k in range(11)]
    from itertools import product
    search_space = product(flux0, flux1)

    control_values = [
        # bottom left
        ((0.0, 0.366), 1580.0),
        # top left
        ((0.0, 0.411), 1495.0),
        # top middle
        ((0.038, 0.411), 1511.0),
        # top right
        ((0.076, 0.411), 1540.0)
        ]
    tol = 20.0

    first = True
    best_match = None
    best_match_norm = None
    for p in search_space:
        print(p)
        # for boundary conditions
        flux = {'upper': p[0],
                'crucible': p[1]}

        theta = test_solve(flux, stationary=True)

        # Check if temperature values match with crucible blueprint.
        dev = np.array([theta(c[0]) - c[1] for c in control_values])
        print('Deviations from control temperatures:')
        print(dev)
        dev_norm = np.linalg.norm(dev)
        if not best_match or dev_norm < best_match_norm:
            print('New best match! %r (||dev|| = %e)' % (p, dev_norm))
            best_match = p
            best_match_norm = dev_norm

        if all(abs(dev) < tol):
            print('Success! %r' % p)
            print('Temperature at control points (with reference values):')
            for c in control_values:
                print('(%e, %e):  %e   (%e)'
                      % (c[0][0], c[0][1], theta(c[0]), c[1]))
        print()

        if first:
            theta_1 = theta
            first = False
        else:
            theta_1.assign(theta)
        plot(theta_1, rescale=True)
        interactive()
    return


def _get_weak_F(mesh, f, kappa, rho_cp, b,
                theta_bcs_n,
                theta_bcs_r):
    '''Get right-hand side of heat equation in weak form.
    '''
    r = SpatialCoordinate(mesh)[0]
    n = FacetNormal(mesh)

    # pylint: disable=unused-argument
    def weak_F(t, u_t, u, v):
        f.t = t
        F = - r * kappa * dot(grad(u), grad(v/rho_cp)) * 2*pi*dx \
            - dot(b, grad(u)) * v * 2*pi*r*dx \
            + f * v/rho_cp * 2*pi*r*dx
        # Neumann boundary conditions
        for k, gradT in theta_bcs_n.items():
            F += r * kappa * dot(n, gradT) * v/rho_cp * 2*pi*ds(k)
        # Robin boundary conditions
        for k, value in theta_bcs_r.items():
            alpha, u0 = value
            F -= r * kappa * alpha * (u - u0) * v/rho_cp * 2*pi*ds(k)
        return F
    return weak_F


@pytest.mark.parametrize('stationary', [
    True, False
    ])
def test_solve(stationary):

    problem = problems.Crucible()

    #
    mesh = problem.W.mesh()
    boundaries = problem.wp_boundaries

    background_temp = 1500.0
    theta0 = Constant(background_temp)

    f = Constant(0.0)

    material = problem.subdomain_materials[problem.wpi]
    rho = material.density(background_temp)
    cp = material.specific_heat_capacity
    kappa = material.thermal_conductivity

    dt = 1.0e1
    end_time = 1000.0

    my_ds = Measure('ds')(subdomain_data=boundaries)

    b = Constant((0.0, 0.0))

    # b_tau = stab.supg2(Q.mesh(),
    #                    b,
    #                    kappa/rho_cp,
    #                    Q.ufl_element().degree()
    #                    )
    # The corresponding operator in weak form.
    rho_cp = rho*cp
    r = SpatialCoordinate(mesh)[0]

    # pylint: disable=unused-argument
    def weak_F(t, u_t, u, v):
        f.t = t
        F = - r * kappa * dot(grad(u), grad(v/rho_cp)) * 2*pi*dx \
            - dot(b, grad(u)) * v * 2*pi*r*dx \
            + f * v/rho_cp * 2*pi*r*dx
        # Neumann boundary conditions
        for k, n_grad_T in problem.theta_bcs_n.items():
            F += r * kappa * n_grad_T * v/rho_cp * 2*pi*my_ds(k)
        # Robin boundary conditions
        for k, value in problem.theta_bcs_r.items():
            alpha, u0 = value
            F -= r * kappa * alpha * (u - u0) * v/rho_cp * 2*pi*my_ds(k)
        return F

    if stationary:
        t = 0.0
        # u_t = Constant(0.0)
        u = TrialFunction(problem.Q)
        v = TestFunction(problem.Q)
        steady_F = weak_F(None, None, u, v)
        a = lhs(steady_F)
        L = rhs(steady_F)
        # Solve the stationary problem with the strict Dirichlet boundary
        # conditions, and extract dtheta/dn from the solution.
        theta_reference = Function(problem.Q, name='temperature')
        solve(a == L,
              theta_reference,
              bcs=problem.theta_bcs_d,
              # bcs=theta_bcs_d_strict,
              solver_parameters={
                  'linear_solver': 'iterative',
                  'symmetric': False,
                  # AMG won't work well if the convection is too strong.
                  'preconditioner': 'hypre_amg',
                  'krylov_solver': {
                      'relative_tolerance': 1.0e-12,
                      'absolute_tolerance': 0.0,
                      'maximum_iterations': 100,
                      'monitor_convergence': False
                      }
                  })

        with XDMFFile('temperature.xdmf') as f:
            f.parameters['flush_output'] = True
            f.parameters['rewrite_function_mesh'] = False
            f.write(theta_reference)

        plot(theta_reference, rescale=True)
        interactive()
        exit()
        # Create new Dirichlet boundary conditions with the reference
        # solution.
        theta_bcs_d_new = []
        for d in problem.theta_bcs_d:
            V = d.function_space()
            theta_bcs_d_new.append(
                DirichletBC(V, theta_reference, d.domain_args[0])
                )
        theta = Function(problem.Q)
        solve(a == L,
              theta,
              bcs=theta_bcs_d_new,
              solver_parameters={
                  'linear_solver': 'iterative',
                  'symmetric': False,
                  'preconditioner': 'hypre_amg',
                  'krylov_solver': {
                      'relative_tolerance': 1.0e-12,
                      'absolute_tolerance': 0.0,
                      'maximum_iterations': 100,
                      'monitor_convergence': False
                      }
                  })

        with XDMFFile('temperature.xdmf') as f:
            f.parameters['flush_output'] = True
            f.parameters['rewrite_function_mesh'] = False
            f.write(theta_reference)

        plot(theta_reference)
        interactive()
        return theta
    else:
        theta_1 = Function(problem.Q)
        theta_1.interpolate(theta0)
        t = 0.0
        with XDMFFile('temperature.xdmf') as f:
            f.parameters['flush_output'] = True
            f.parameters['rewrite_function_mesh'] = False

            while t < end_time:
                theta = ts.implicit_euler_step(
                        problem.Q,
                        weak_F,
                        theta_1,
                        t, dt,
                        dx=2*pi*r*dx,
                        sympy_dirichlet_bcs=problem.theta_bcs_d,
                        tol=1.0e-10,
                        verbose=False,
                        # problem is symmetric =>
                        krylov='cg',
                        preconditioner='hypre_amg'
                        )
                theta_1.assign(theta)
                f.write(theta_1, t)
                plot(theta_1, title='temperature')
                # interactive()
                t += dt
    return


if __name__ == '__main__':
    _main()
    # _parameter_quest()

# -*- coding: utf-8 -*-
#
#  Copyright (c) 2012--2014, Nico Schlömer, <nico.schloemer@gmail.com>
#  All rights reserved.
#
#  This file is part of Maelstrom.
#
#  Maelstrom is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  Maelstrom is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with Maelstrom.  If not, see <http://www.gnu.org/licenses/>.
#
'''
The equation system defined in this routine are largely based on
:cite:`Cha97`.  The equations had to be modified to include the material
flux :math:`u`, modeled by Navier-Stokes.  In the setup with convections, the
current density is expressed by

.. math::
     J = \sigma (E + u \\times B)

which leads to

.. math::
     curl(\sigma^{-1} (J - u\\times B) + i \omega A) = 0.

Since :math:`u` only has components in :math:`r`- and :math:`z`-direction,

.. math::
     u = u_r e_r + u_z e_z,

and :math:`B = curl(\phi e_{\\theta})`, we end up with

.. math::
    u \\times B &= u \\times curl(\phi e_{\\theta}) \\\\
                &= u \\times \left(- \\frac{d\phi}{dz} e_r + \\frac{1}{r}
                \\frac{d(r\phi)}{dr} e_z\\right) \\\\
                &= -u_z \\frac{d\phi}{dz} e_{\\theta} - u_r \\frac{1}{r}
                \\frac{d(r\phi)}{dr} e_{\\theta}.

Following Chaboudez, this eventually leads to the equation system

.. math::
    \\begin{cases}
    - div\left(\\frac{1}{\mu r} \\nabla(r\phi)\\right) + \left\langle u,
      \\frac{1}{r}\\nabla(r\phi)\\right\\rangle + i \sigma \omega \phi
    = \\frac{\sigma v_k}{2\pi r}    \quad\\text{in } \Omega,\\\\
    n\cdot\left(- \\frac{1}{\mu r} \\nabla(r\phi)\\right) = 0    \quad\\text{on
    }\Gamma \setminus \{r=0\}\\\\
    \phi = 0    \quad\\text{ for } r=0.
    \\end{cases}

The differential operators are interpreted like 2D for :math:`r` and :math:`z`.
The seemingly complex additional term :math:`u\\times B` finally breaks down
to just a convective component.

For the weak formulation, the volume elements :math:`2\pi r dx` are used. This
corresponds to the full 3D rotational formulation and also makes
sure that at least the diffusive term is nice and symmetric. Additionally, it
avoids dividing by r in the convections and the right hand side.

.. math::
       \int div\left(\\frac{1}{\mu r} \\nabla(r u)\\right) (2\pi r v)
     + \langle b, \\nabla(r u)\\rangle 2\pi v
     + i \sigma \omega u 2 \pi r v
   = \int \sigma v_k v.
'''
from dolfin import info, Expression, triangle, plot, interactive, \
    DOLFIN_EPS, DirichletBC, Function, TestFunction, \
    TrialFunction, solve, zero, norm, KrylovSolver, dot, grad, pi, \
    TrialFunctions, TestFunctions, assemble, div, Constant, project, \
    FunctionSpace, sqrt, MPI, MeshFunction
import numpy

from message import Message

#parameters.linear_algebra_backend = 'uBLAS'
#from scipy.sparse import csr_matrix
#from betterspy import betterspy


# Don't rename to solve() -- that method already exists in Dolfin. :/
def solve_maxwell(V, dx,
                  Mu, Sigma,  # dictionaries
                  omega,
                  f_list,  # list of dictionaries
                  convections,  # dictionary
                  bcs=None,
                  tol=1.0e-12,
                  compute_residuals=True,
                  verbose=False
                  ):
    '''Solve the complex-valued time-harmonic Maxwell system in 2D cylindrical
    coordinates.

    :param V: function space for potentials
    :param dx: measure
    :param omega: current frequency
    :type omega: float
    :param f_list: list of right-hand sides
    :param convections: convection terms by subdomains
    :type convections: dictionary
    :param bcs: Dirichlet boundary conditions
    :param tol: solver tolerance
    :type tol: float
    :param verbose: solver verbosity
    :type verbose: boolean
    :rtype: list of functions
    '''
    # For the exact solution of the magnetic scalar potential, see
    # <http://www.physics.udel.edu/~jim/PHYS809_10F/Class_Notes/Class_26.pdf>.
    # Here, the value of \phi along the rotational axis is specified as
    #
    #    phi(z) = 2 pi I / c * (z/|z| - z/sqrt(z^2 + a^2))
    #
    # where 'a' is the radius of the coil. This expression contradicts what is
    # specified by [Chaboudez97]_ who claim that phi=0 is the natural value
    # at the symmetry axis.
    #
    # For more analytic expressions, see
    #
    #     Simple Analytic Expressions for the Magnetic Field of a Circular
    #     Current Loop;
    #     James Simpson, John Lane, Christopher Immer, and Robert Youngquist;
    #     <http://ntrs.nasa.gov/archive/nasa/casi.ntrs.nasa.gov/20010038494_2001057024.pdf>.
    #

    # Check if boundary conditions on phi are explicitly provided.
    if not bcs:
        # Create Dirichlet boundary conditions.
        # In the cylindrically symmetric formulation, the magnetic vector
        # potential is given by
        #
        #    A = e^{i omega t} phi(r,z) e_{theta}.
        #
        # It is natural to demand phi=0 along the symmetry axis r=0 to avoid
        # discontinuities there.
        # Also, this makes sure that the system is well-defined (see comment
        # below).
        #
        def xzero(x, on_boundary):
            return on_boundary and abs(x[0]) < DOLFIN_EPS
        bcs = DirichletBC(V * V, (0.0, 0.0), xzero)
        #
        # Concerning the boundary conditions for the rest of the system:
        # At the other boundaries, it is not uncommon (?) to set so-called
        # impedance boundary conditions; see, e.g.,
        #
        #    Chaboudez et al.,
        #    Numerical Modeling in Induction Heating for Axisymmetric
        #    Geometries,
        #    IEEE Transactions on Magnetics, vol. 33, no. 1, Jan 1997,
        #    <http://www.esi-group.com/products/casting/publications/Articles_PDF/InductionaxiIEEE97.pdf>.
        #
        # or
        #
        #    <ftp://ftp.math.ethz.ch/pub/sam-reports/reports/reports2010/2010-39.pdf>.
        #
        # TODO review those, references don't seem to be too accurate
        # Those translate into Robin-type boundary conditions (and are in fact
        # sometimes called that, cf.
        # https://en.wikipedia.org/wiki/Robin_boundary_condition).
        # The classical reference is
        #
        #     Impedance boundary conditions for imperfectly conducting
        #     surfaces,
        #     T.B.A. Senior,
        #     <http://link.springer.com/content/pdf/10.1007/BF02920074>.
        #
        #class OuterBoundary(SubDomain):
        #    def inside(self, x, on_boundary):
        #        return on_boundary and abs(x[0]) > DOLFIN_EPS
        #boundaries = FacetFunction('size_t', mesh)
        #boundaries.set_all(0)
        #outer_boundary = OuterBoundary()
        #outer_boundary.mark(boundaries, 1)
        #ds = Measure('ds')[boundaries]
        ##n = FacetNormal(mesh)
        ##a += - 1.0/Mu[i] * dot(grad(r*ur), n) * vr * ds(1) \
        ##     - 1.0/Mu[i] * dot(grad(r*ui), n) * vi * ds(1)
        ##L += - 1.0/Mu[i] * 1.0 * vr * ds(1) \
        ##     - 1.0/Mu[i] * 1.0 * vi * ds(1)
        ## This is -n.grad(r u) = u:
        #a += 1.0/Mu[i] * ur * vr * ds(1) \
        #   + 1.0/Mu[i] * ui * vi * ds(1)

    # Create the system matrix, preconditioner, and the right-hand sides.
    # For preconditioners, there are two approaches. The first one, described
    # in
    #
    #     Algebraic Multigrid for Complex Symmetric Systems;
    #     D. Lahaye, H. De Gersem, S. Vandewalle, and K. Hameyer;
    #     <https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber=877730>
    #
    # doesn't work too well here.
    # The matrix P, created in _build_system(), provides a better alternative.
    # For more details, see documentation in _build_system().
    #
    A, P, b_list, M, W = _build_system(V, dx,
                                       Mu, Sigma,  # dictionaries
                                       omega,
                                       f_list,  # list of dicts
                                       convections,  # dict
                                       bcs
                                       )

    #from matplotlib import pyplot as pp
    #rows, cols, values = M.data()
    #from scipy.sparse import csr_matrix
    #M_matrix = csr_matrix((values, cols, rows))
    ##from matplotlib import pyplot as pp
    ###pp.spy(M_matrix, precision=1e-3, marker='.', markersize=5)
    ##pp.spy(M_matrix)
    ##pp.show()
    ## colormap
    #cmap = pp.cm.gray_r
    #M_dense = M_matrix.todense()
    #from matplotlib.colors import LogNorm
    #im = pp.imshow(abs(M_dense), cmap=cmap, interpolation='nearest', norm=LogNorm())
    ##im = pp.imshow(abs(M_dense), cmap=cmap, interpolation='nearest')
    ##im = pp.imshow(abs(A_r), cmap=cmap, interpolation='nearest')
    ##im = pp.imshow(abs(A_i), cmap=cmap, interpolation='nearest')
    #pp.colorbar()
    #pp.show()
    #exit()
    #print A
    #rows, cols, values = A.data()
    #from scipy.sparse import csr_matrix
    #A_matrix = csr_matrix((values, cols, rows))

    ###pp.spy(A_matrix, precision=1e-3, marker='.', markersize=5)
    ##pp.spy(A_matrix)
    ##pp.show()

    ## colormap
    #cmap = pp.cm.gray_r
    #A_dense = A_matrix.todense()
    ##A_r = A_dense[0::2][0::2]
    ##A_i = A_dense[1::2][0::2]
    #cmap.set_bad('r')
    ##im = pp.imshow(abs(A_dense), cmap=cmap, interpolation='nearest', norm=LogNorm())
    #im = pp.imshow(abs(A_dense), cmap=cmap, interpolation='nearest')
    ##im = pp.imshow(abs(A_r), cmap=cmap, interpolation='nearest')
    ##im = pp.imshow(abs(A_i), cmap=cmap, interpolation='nearest')
    #pp.colorbar()
    #pp.show()

    # prepare solver
    solver = KrylovSolver('gmres', 'amg')
    solver.set_operators(A, P)

    # The PDE for A has huge coefficients (order 10^8) all over. Hence, if
    # relative residual is set to 10^-6, the actual residual will still be of
    # the order 10^2. While this isn't too bad (after all the equations are
    # upscaled by a large factor), one can choose a very small relative
    # tolerance here to get a visually pleasing residual norm.
    solver.parameters['relative_tolerance'] = 1.0e-12
    solver.parameters['absolute_tolerance'] = 0.0
    solver.parameters['maximum_iterations'] = 100
    solver.parameters['report'] = verbose
    solver.parameters['monitor_convergence'] = verbose

    phi_list = []
    for k, b in enumerate(b_list):
        with Message('Computing coil ring %d/%d...' % (k + 1, len(b_list))):
            # Define goal functional for adaptivity.
            # Adaptivity not working for subdomains, cf.
            # https://bugs.launchpad.net/dolfin/+bug/872105.
            #(phi_r, phi_i) = split(phi)
            #M = (phi_r*phi_r + phi_i*phi_i) * dx(2)
            phi_list.append(Function(W))
            phi_list[-1].rename('phi%d' % k, 'phi%d' % k)
            solver.solve(phi_list[-1].vector(), b)

        ## Adaptive mesh refinement.
        #_adaptive_mesh_refinement(dx,
        #                          phi_list[-1],
        #                          Mu, Sigma, omega,
        #                          convections,
        #                          f_list[k]
        #                          )
        #exit()

        if compute_residuals:
            # Sanity check: Compute residuals.
            # This is quite the good test that we haven't messed up
            # real/imaginary in the above formulation.
            r_r, r_i = _build_residuals(V, dx, phi_list[-1],
                                        omega, Mu, Sigma,
                                        convections, voltages
                                        )

            def xzero(x, on_boundary):
                return on_boundary and abs(x[0]) < DOLFIN_EPS

            subdomain_indices = Mu.keys()

            # Solve an FEM problem to get the corresponding residual function
            # out.
            # This is exactly what we need here! :)
            u = TrialFunction(V)
            v = TestFunction(V)
            a = zero() * dx(0)
            for i in subdomain_indices:
                a += u * v * dx(i)

            # TODO don't hard code the boundary conditions like this
            R_r = Function(V)
            solve(a == r_r, R_r,
                  bcs=DirichletBC(V, 0.0, xzero)
                  )

            # TODO don't hard code the boundary conditions like this
            R_i = Function(V)
            solve(a == r_i, R_i,
                  bcs=DirichletBC(V, 0.0, xzero)
                  )

            nrm_r = norm(R_r)
            info('||r_r|| = %e' % nrm_r)
            nrm_i = norm(R_i)
            info('||r_i|| = %e' % nrm_i)
            res_norm = sqrt(nrm_r * nrm_r + nrm_i * nrm_i)
            info('||r|| = %e' % res_norm)

            plot(R_r, title='R_r')
            plot(R_i, title='R_i')
            interactive()
            #exit()
    return phi_list


def _build_residuals(V, dx, phi, omega, Mu, Sigma, convections, voltages):
    #class OuterBoundary(SubDomain):
    #    def inside(self, x, on_boundary):
    #        return on_boundary and abs(x[0]) > DOLFIN_EPS
    #boundaries = FacetFunction('size_t', mesh)
    #boundaries.set_all(0)
    #outer_boundary = OuterBoundary()
    #outer_boundary.mark(boundaries, 1)
    #ds = Measure('ds')[boundaries]

    r = Expression('x[0]', domain=V.mesh())

    subdomain_indices = Mu.keys()

    #u = TrialFunction(V)
    v = TestFunction(V)

    r_r = zero() * dx(0)
    for i in subdomain_indices:
        r_r += 1.0 / (Mu[i] * r) * dot(grad(r * phi[0]), grad(r * v)) * 2 * pi * dx(i) \
            - omega * Sigma[i] * phi[1] * v * 2 * pi * r * dx(i)
    # convections
    for i, conv in convections.items():
        r_r += dot(conv, grad(r * phi[0])) * v * 2 * pi * dx(i)
    # rhs
    for i, voltage in voltages.items():
        r_r -= Sigma[i] * voltage.real * v * dx(i)
    ## boundaries
    #r_r += 1.0/Mu[i] * phi[0] * v * 2*pi*ds(1)

    # imaginary part
    r_i = zero() * dx(0)
    for i in subdomain_indices:
        r_i += 1.0 / (Mu[i] * r) * dot(grad(r * phi[1]), grad(r * v)) * 2 * pi * dx(i) \
            + omega * Sigma[i] * phi[0] * v * 2 * pi * r * dx(i)
    # convections
    for i, conv in convections.items():
        r_i += dot(conv, grad(r * phi[1])) * v * 2 * pi * dx(i)
    # rhs
    for i, voltage in voltages.items():
        r_r -= Sigma[i] * voltage.imag * v * dx(i)
    ## boundaries
    #r_i += 1.0/Mu[i] * phi[1] * v * 2*pi*ds(1)

    return r_r, r_i


def _build_system(V, dx,
                  Mu, Sigma,  # dictionaries
                  omega,
                  f_list,  # list of dictionaries
                  convections,  # dictionary
                  bcs
                  ):
    '''Build FEM system for

    .. math::
         div\\left(\\frac{1}{\mu r} \\nabla(r\phi)\\right)
         + \langle u, 1/r \\nabla(r\phi)\\rangle + i \sigma \omega \phi
            = f

    by multiplying with :math:`2\pi r v` and integrating over the domain.
    '''
    r = Expression('x[0]', domain=V.mesh())

    subdomain_indices = Mu.keys()

    W = V * V

    # Bilinear form.
    (ur, ui) = TrialFunctions(W)
    (vr, vi) = TestFunctions(W)

    with Message('Build right-hand sides...'):
        b_list = []
        for f in f_list:
            L = Constant(0.0) * vr * dx(0) \
                + Constant(0.0) * vi * dx(0)
            for i, fval in f.items():
                L += fval[0] * vr * 2*pi*r * dx(i) \
                    + fval[1] * vi * 2*pi*r * dx(i)
            b_list.append(assemble(L))

    # div(1/(mu r) grad(r phi)) + i sigma omega phi
    #
    # Split up diffusive and reactive terms to be able to assemble them with
    # different FFC parameters.
    #
    # Make omega a constant function to avoid rebuilding the equation system
    # when omega changes.
    om = Constant(omega)
    a1 = Constant(0.0) * ur * vr * dx(0)
    a2 = Constant(0.0) * ur * vr * dx(0)
    for i in subdomain_indices:
        # The term 1/r looks like it might cause problems. The dubious term is
        #
        #  1/r d/dr (r u_r) = u_r + 1/r du_r/dr,
        #
        # so we have to make sure that 1/r du_r/dr is bounded for all trial
        # functions u. This is guaranteed when taking Dirichlet boundary
        # conditions at r=0.
        sigma = Constant(Sigma[i])
        a1 += 1.0 / (Mu[i] * r) * dot(grad(r * ur), grad(r * vr)) \
            * 2 * pi * dx(i) \
            + 1.0 / (Mu[i] * r) * dot(grad(r * ui), grad(r * vi)) \
            * 2 * pi * dx(i)
        a2 += - om * sigma * ui * vr * 2*pi*r * dx(i) \
              + om * sigma * ur * vi * 2*pi*r * dx(i)
        # Don't do anything at the interior boundary. Taking the Poisson
        # problem as an example, the weak formulation is
        #
        #     \int \Delta(u) v = -\int grad(u).grad(v) + \int_ n.grad(u) v.
        #
        # If we have 'artificial' boundaries through the domain, we would
        # like to make sure that along those boundaries, the equation is
        # exactly what it would be without the them. The important case to
        # look at are the trial and test functions which are nonzero on
        # the boundary. It is clear that the integral along the interface
        # boundary has to be omitted.

    # Add the convective component for the workpiece,
    #   a += <u, 1/r grad(r phi)> *2*pi*r*dx
    for i, conv in convections.items():
        a1 += dot(conv, grad(r * ur)) * vr * 2 * pi * dx(i) \
            + dot(conv, grad(r * ui)) * vi * 2 * pi * dx(i)

    force_m_matrix = False
    if force_m_matrix:
        A1 = assemble(a1)
        A2 = assemble(a2,
                      form_compiler_parameters={'quadrature_rule': 'vertex',
                                                'quadrature_degree': 1})
        A = A1 + A2
    else:
        # Assembling the thing into one single object makes it possible to
        # extract .data() for conversion to SciPy's sparse types later.
        A = assemble(a1 + a2)

    # Compute the preconditioner as described in
    #
    #     A robust preconditioned MINRES-solver
    #     for time-periodic eddy-current problems;
    #     M. Kolmbauer, U. Langer;
    #     <http://www.numa.uni-linz.ac.at/Publications/List/2012/2012-02.pdf>.
    #
    # For the real-imag system
    #
    #     ( K  M )
    #     (-M  K ),
    #
    # Kolmbauer and Langer suggest the preconditioner
    #
    #     ( K+M        )
    #     (     -(K+M) ).
    #
    # The diagonal blocks can, for example, be solved with standard AMG
    # methods.
    p1 = Constant(0.0) * ur * vr * dx(0)
    p2 = Constant(0.0) * ur * vr * dx(0)
    # Diffusive terms.
    for i in subdomain_indices:
        p1 += 1.0 / (Mu[i]*r) * dot(grad(r*ur), grad(r*vr)) * 2 * pi * dx(i) \
            - 1.0 / (Mu[i]*r) * dot(grad(r*ui), grad(r*vi)) * 2 * pi * dx(i)
        p2 += om * Constant(Sigma[i]) * ur * vr * 2 * pi * r * dx(i) \
            - om * Constant(Sigma[i]) * ui * vi * 2 * pi * r * dx(i)
    P = assemble(p1 + p2)
    #P = assemble(p1)
    #P2 = assemble(p2)
    #P = P1 + P2

    # build mass matrix
    #mm = sum([(ur * vr + ui * vi) * 2*pi*r * dx(i)
    #          for i in subdomain_indices
    #          ])
    mm = Constant(0.0) * ur * vr * dx(0)
    for i in subdomain_indices:
        mm += ur * vr * 2*pi*r * dx(i) \
            + ui * vi * 2*pi*r * dx(i)
    M = assemble(mm)

    # Apply boundary conditions.
    if bcs:
        bcs.apply(A)
        bcs.apply(P)
        bcs.apply(M)
        for b in b_list:
            bcs.apply(b)

    #rows, cols, values = A.data()
    #A = csr_matrix((values, cols, rows))
    #print A
    #betterspy(A)
    #from matplotlib import pyplot as pp
    #pp.show()
    #exx

    #M2 = assemble(m)

    #Mdiff = M - M2

    #print Mdiff

    #from matplotlib import pyplot as pp
    #rows, cols, values = Mdiff.data()
    #print len(rows)
    #print rows
    #pp.plot(rows)
    #pp.show()
    #print len(cols)
    #print cols
    #print len(values)
    #print values
    #from scipy.sparse import csr_matrix
    #M_matrix = csr_matrix((values, cols, rows))
    #print M_matrix
    ###from matplotlib import pyplot as pp
    ####pp.spy(M_matrix, precision=1e-3, marker='.', markersize=5)
    ###pp.spy(M_matrix)
    ###pp.show()
    ### colormap
    ##cmap = pp.cm.gray_r
    ##M_dense = M_matrix.todense()
    ##from matplotlib.colors import LogNorm
    ##im = pp.imshow(abs(M_dense), cmap=cmap, interpolation='nearest', norm=LogNorm())
    ###im = pp.imshow(abs(M_dense), cmap=cmap, interpolation='nearest')
    ###im = pp.imshow(abs(A_r), cmap=cmap, interpolation='nearest')
    ###im = pp.imshow(abs(A_i), cmap=cmap, interpolation='nearest')
    ##pp.colorbar()
    ##pp.show()
    #exit()

    return A, P, b_list, M, W


def prescribe_current(A, b, coil_rings, current):
    '''Get the voltage coefficients c_l with the total current prescribed.
    '''
    A[coil_rings][:] = 0.0
    for i in coil_rings:
        A[i][i] = 1.0
    # The current must equal in all coil rings.
    b[coil_rings] = current
    return A, b


def prescribe_voltage(A, b, coil_rings, voltage, v_ref, J):
    '''Get the voltage coefficients c_l with the total voltage prescribed.
    '''
    # The currents must equal in all coil rings.
    for k in range(len(coil_rings) - 1):
        i = coil_rings[k]
        i1 = coil_rings[k + 1]
        A[i][:] = J[i][:] - J[i1][:]
        b[i] = 0.0
    # sum c_k * v_ref == V
    i = coil_rings[-1]
    A[i][:] = 0.0
    A[i][coil_rings] = v_ref
    b[i] = voltage
    return A, b


def prescribe_power(A, b, coil_rings, total_power, v_ref, J):
    '''Get the voltage coefficients c_l with the total power prescribed.
    '''
    raise RuntimeError('Not yet implemented.')
    # There are different notions of power for AC current; for an overview, see
    # [1]. With
    #
    #     v(t) = V exp(i omega t),
    #     i(t) = I exp(i omega t),
    #
    # V, I\in\C, we have
    #
    #     p(t) = v(t) * i(t).
    #
    # The time-average over one period is
    #
    #     P = 1/2 Re(V I*)
    #       = Re(V_RMS I_RMS*)
    #
    # with the root-mean-square (RMS) quantities. This corresponds wit the
    # _real power_ in [1].
    # When assuming that the voltage is real-valued, the power is
    #
    #    P = V/2 Re(I).
    #
    # [1] <https://en.wikipedia.org/wiki/AC_power>.
    #
    voltage = v_ref
    A, b = prescribe_voltage(A, b, coil_rings, voltage, v_ref, J)

    # Unconditionally take J[0] here. -- It shouldn't make a difference.
    alpha = numpy.sqrt(2 * total_power / (v_ref * numpy.sum(J[0][:] * c).real))
    # We would like to scale the solution with alpha. For this, scale the
    # respective part of the right-hand side.
    b[coils] *= alpha

    return A, b


def compute_potential(coils, V, dx, mu, sigma, omega, convections,
                      verbose=True,
                      io_submesh=None
                      ):
    '''Compute the magnetic potential :math:`\Phi` with
    :math:`A = \exp(i \omega t) \Phi e_{\\theta}` for a number of coils.
    '''
    # Index all coil rings consecutively, starting with 0.
    # This makes them easier to handle for the equation system.
    physical_indices = []
    new_coils = []
    k = 0
    for coil in coils:
        new_coils.append([])
        for coil_ring in coil['rings']:
            new_coils[-1].append(k)
            physical_indices.append(coil_ring)
            k += 1

    # Set arbitrary reference voltage.
    v_ref = 1.0

    r = Expression('x[0]', domain=V.mesh())

    # Compute reference potentials for all coil rings.
    # Prepare the right-hand sides according to :cite:`Cha97`.
    f_list = []
    for k in physical_indices:
        # Real an imaginary parts.
        f_list.append({k: (v_ref * sigma[k] / (2 * pi * r), Constant(0.0))})
    # Solve.
    phi_list = solve_maxwell(V, dx,
                             mu, sigma,
                             omega,
                             f_list,
                             convections,
                             tol=1.0e-12,
                             compute_residuals=False,
                             verbose=True
                             )

    # Write out these phi's to files.
    if io_submesh:
        V_submesh = FunctionSpace(io_submesh, 'CG', 1)
        W_submesh = V_submesh * V_submesh
        from dolfin import interpolate, XDMFFile
        for k, phi in enumerate(phi_list):
            # Restrict to workpiece submesh.
            phi_out = interpolate(phi, W_submesh)
            phi_out.rename('phi%02d' % k, 'phi%02d' % k)
            # Write to file
            phi_file = XDMFFile(io_submesh.mpi_comm(), 'phi%02d.xdmf' % k)
            phi_file.parameters['flush_output'] = True
            phi_file << phi_out
            #plot(phi_out)
            #interactive()

    # Compute weights for the individual coils.
    # First get the voltage--coil-current mapping.
    J = get_voltage_current_matrix(phi_list, physical_indices, dx,
                                   sigma,
                                   omega,
                                   v_ref
                                   )

    num_coil_rings = len(phi_list)
    A = numpy.empty((num_coil_rings, num_coil_rings), dtype=J.dtype)
    b = numpy.empty(num_coil_rings, dtype=J.dtype)
    for k, coil in enumerate(new_coils):
        weight_type = coils[k]['c_type']
        target_value = coils[k]['c_value']
        if weight_type == 'current':
            A, b = prescribe_current(A, b, coil, target_value)
        elif weight_type == 'voltage':
            A, b = prescribe_voltage(A, b, coil, target_value, v_ref, J)
        else:
            raise RuntimeError('Illegal weight type \'%r\'.' % weight_type)

    # TODO write out the equation system to a file
    if io_submesh:
        numpy.savetxt('matrix.dat', A)
    exit()

    # Solve the system for the weights.
    weights = numpy.linalg.solve(A, b)

    ## Prescribe total power.
    #target_total_power = 4.0e3
    ## Compute all coils with reference voltage.
    #num_coil_rings = J.shape[0]
    #A = numpy.empty((num_coil_rings, num_coil_rings), dtype=J.dtype)
    #b = numpy.empty(num_coil_rings)
    #for k, coil in enumerate(new_coils):
    #    target_value = v_ref
    #    A, b = prescribe_voltage(A, b, coil, target_value, v_ref, J)
    #weights = numpy.linalg.solve(A, b)
    #preliminary_voltages = v_ref * weights
    #preliminary_currents = numpy.dot(J, weights)
    ## Compute resulting total power.
    #total_power = 0.0
    #for coil_loops in new_coils:
    #    # Currents should be the same all over the entire coil,
    #    # so take currents[coil_loops[0]].
    #    total_power += 0.5 \
    #                 * numpy.sum(preliminary_voltages[coil_loops]) \
    #                 * preliminary_currents[coil_loops[0]].real
    #                 # TODO no abs here
    ## Scale all voltages by necessary factor.
    #weights *= numpy.sqrt(target_total_power / total_power)

    if verbose:
        info('')
        info('Resulting voltages,   V/sqrt(2):')
        voltages = v_ref * weights
        info('   %r' % (abs(voltages) / numpy.sqrt(2)))
        info('Resulting currents,   I/sqrt(2):')
        currents = numpy.dot(J, weights)
        info('   %r' % (abs(currents) / numpy.sqrt(2)))
        info('Resulting apparent powers (per coil):')
        for coil_loops in new_coils:
            # With
            #
            #     v(t) = Im(exp(i omega t) v),
            #     i(t) = Im(exp(i omega t) i),
            #
            # the average apparent power over one period is
            #
            #     P_av = omega/(2 pi) int_0^{2 pi/omega} v(t) i(t)
            #          = 1/2 Re(v i*).
            #
            # Currents should be the same all over, so take currents[coil[0]].
            #
            alpha = sum(voltages[coil_loops]) \
                * currents[coil_loops[0]].conjugate()
            power = 0.5 * alpha.real
            info('   %r' % power)
        info('')

    # Compute Phi as the linear combination \sum C_i*phi_i.
    # The function Phi is guaranteed to fulfill the PDE as well (iff the
    # the boundary conditions are linear in phi too).
    #
    # Form $\sum_l c_l \phi_l$.
    # https://answers.launchpad.net/dolfin/+question/214172
    #
    # Unfortunately, one cannot just use
    #     Phi[0].vector()[:] += c.real * phi[0].vector()
    # since phi is from the FunctionSpace V*V and thus .vector() is not
    # available for the individual components.
    #
    Phi = [Constant(0.0),
           Constant(0.0)]
    for phi, c in zip(phi_list, weights):
        # Phi += c * phi
        Phi[0] += c.real * phi[0] - c.imag * phi[1]
        Phi[1] += c.imag * phi[0] + c.real * phi[1]

    # Project the components down to V. This makes various subsequent
    # computations with Phi faster.
    Phi[0] = project(Phi[0], V)
    Phi[0].rename('Re(Phi)', 'Re(Phi)')
    Phi[1] = project(Phi[1], V)
    Phi[1].rename('Im(Phi)', 'Im(Phi)')
    return Phi, voltages


def get_voltage_current_matrix(phi, physical_indices, dx,
                               Sigma,
                               omega,
                               v_ref
                               ):
    '''Compute the matrix that relates the voltages with the currents in the
    coil rings. (The relationship is indeed linear.)

    This is according to :cite:`KP02`.

    The entry :math:`J_{k,l}` in the resulting matrix is the contribution of
    the potential generated by coil :math:`l` to the current in coil :math:`k`.
    '''
    mesh = phi[0].function_space().mesh()
    r = Expression('x[0]', domain=mesh)

    num_coil_rings = len(phi)
    J = numpy.empty((num_coil_rings, num_coil_rings), dtype=numpy.complex)
    for l, pi0 in enumerate(physical_indices):
        partial_phi_r, partial_phi_i = phi[l].split()
        for k, pi1 in enumerate(physical_indices):
            # -1i*omega*int_{coil_k} sigma phi.
            int_r = assemble(Sigma[pi1] * partial_phi_r * dx(pi1))
            int_i = assemble(Sigma[pi1] * partial_phi_i * dx(pi1))
            J[k][l] = -1j * omega * (int_r + 1j * int_i)
        # v_ref/(2*pi) * int_{coil_l} sigma/r.
        # 1/r doesn't explode since we only evaluate it in the coils where
        # r!=0.
        # For assemble() to work, a mesh needs to be supplied either implicitly
        # by the integrand, or explicitly. Since the integrand doesn't contain
        # mesh information here, pass it through explicitly.
        J[l][l] += v_ref / (2 * pi) \
            * assemble(Sigma[pi0] / r * dx(pi0), mesh=mesh)
    return J


def compute_joule(Phi, voltages,
                  omega, Sigma, Mu,
                  subdomain_indices
                  ):
    '''Compute Joule heating term and Lorentz force from given coil voltages.
    '''
    #j_r = {}
    #j_i = {}
    #E_r =  omega*Phi_i + rhs_r
    #E_i = -omega*Phi_r + rhs_i
    #plot(E_r)
    #plot(E_i)
    #interactive()
    #exit()
    # The Joule heating source is
    # https://en.wikipedia.org/wiki/Joule_heating#Differential_Form
    #
    #   P = J.E =  \sigma E.E.
    #
    #joule_source = zero() * dx(0)
    joule_source = {}
    r = Expression('x[0]', domain=Phi[0].function_space().mesh())
    for i in subdomain_indices:
        # See, e.g., equation (2.17) in
        #
        #     Numerical modeling in induction heating
        #     for axisymmetric geometries,
        #     Chaboudez et al.,
        #     IEEE Transactions of magnetics, vol. 33, no. 1, January 1997.
        #
        # In a time-harmonic approximation with
        #     A = Re(a exp(i omega t)),
        #     B = Re(b exp(i omega t)),
        # the time-average of $A\cdot B$ over one period is
        #
        #    \overline{A\cdot B} = 1/2 Re(a \cdot b*)
        #
        # see <http://www.ece.rutgers.edu/~orfanidi/ewa/ch01.pdf>.
        # In particular,
        #
        #    \overline{A\cdot A} = 1/2 ||a||^2
        #
        # Consequently, we can compute the average source term over one period
        # as
        #
        #     source = 1/2 ||j||^2 / sigma = 1/2 * ||E||^2 * sigma.
        #
        # (Not using j avoids explicitly dividing by sigma which is 0 at
        # nonconductors.)
        #
        # TODO check this part
        E_r = +omega * Phi[1]
        E_i = -omega * Phi[0]
        if i in voltages:
            E_r += voltages[i].real / (2*pi*r)
            E_i += voltages[i].imag / (2*pi*r)
        joule_source[i] = 0.5 * Sigma[i] * (E_r*E_r + E_i*E_i)

    ## Alternative computation.
    #joule_source = zero() * dx(0)
    #for i in subdomain_indices:
    #    joule_source += 1.0/(Mu[i]*r) * dot(grad(r*Phi_r),grad(v)) * dx(i)

    ## And the third way (for omega==0)
    #joule_source = zero() * dx(0)
    #for i in subdomain_indices:
    #    if i in C:
    #        joule_source += Sigma[i] * voltages[i].real / (2*pi*r) * v * dx(i)
    #u = TrialFunction(V)
    #sol = Function(V)
    #solve(u*v*dx() == joule_source, sol,
    #      bcs = DirichletBC(V, 0.0, 'on_boundary'))
    #plot(sol)
    #interactive()
    #exit()
    return joule_source


def compute_lorentz(Phi, omega, sigma):
    '''
    In a time-harmonic discretization with

    .. math::
        A &= \Re(a \exp(i \omega t)),\\\\
        B &= \Re(b \exp(i \omega t)),

    the time-average of :math:`A\\times B` over one period is

    .. math::
        \overline{A\\times B} = \\frac{1}{2} \Re(a \\times b^*),

    see <http://www.ece.rutgers.edu/~orfanidi/ewa/ch01.pdf>.
    Since the Lorentz force generated by the current :math:`J` in the magnetic
    field :math:`B` is

    .. math::
        F_L = J \\times B,

    its time average is

    .. math::
       \overline{F_L} = \\frac{1}{2} \Re(j \\times b^*).

    With

    .. math::
       J &= \exp(i \omega t) j e_{\\theta},\\\\
       B &= \exp(i \omega t) \left(-\\frac{d\phi}{dz} e_r + \\frac{1}{r}
            \\frac{d(r\phi)}{dr} e_z\\right),

    we have

    .. math::
       \overline{F_L} &= \\frac{1}{2} \Re\left(j \\frac{d\phi}{dz} e_z + \\frac{j}{r}
       \\frac{d(r\phi)}{dr} e_r\\right)\\\\
                      &= \\frac{1}{2} \Re\\left(\\frac{j}{r} \\nabla(r\phi^*)\\right)\\\\
                      &= \\frac{1}{2} \\left(\\frac{\Re(j)}{r} \\nabla(r \Re(\phi))
                             +\\frac{\Im(j)}{r} \\nabla(r \Im(\phi))\\right)

    Only create the Lorentz force for the workpiece. This avoids
    complications with j(r,z) for which we here can assume

    .. math::
        j = -i \sigma \omega \phi

    (in particular not containing a voltage term).
    '''
    r = Expression('x[0]', domain=Phi[0].function_space().mesh())
    j_r = + sigma * omega * Phi[1]
    j_i = - sigma * omega * Phi[0]
    return 0.5 * (j_r / r * grad(r * Phi[0])
                 +j_i / r * grad(r * Phi[1]))


def _adaptive_mesh_refinement(dx, phi, mu, sigma, omega, conv, voltages):
    from dolfin import cells, refine
    eta = _error_estimator(dx, phi, mu, sigma, omega, conv, voltages)
    mesh = phi.function_space().mesh()
    level = 0
    TOL = 1.0e-4
    E = sum([e * e for e in eta])
    E = sqrt(MPI.sum(E))
    info('Level %d: E = %g (TOL = %g)' % (level, E, TOL))
    # Mark cells for refinement
    REFINE_RATIO = 0.5
    cell_markers = MeshFunction('bool', mesh, mesh.topology().dim())
    eta_0 = sorted(eta, reverse=True)[int(len(eta) * REFINE_RATIO)]
    eta_0 = MPI.max(eta_0)
    for c in cells(mesh):
        cell_markers[c] = eta[c.index()] > eta_0
    # Refine mesh
    mesh = refine(mesh, cell_markers)
    # Plot mesh
    plot(mesh)
    interactive()
    exit()
    ## Compute error indicators
    #K = array([c.volume() for c in cells(mesh)])
    #R = array([abs(source([c.midpoint().x(), c.midpoint().y()])) for c in cells(mesh)])
    #gam = h*R*sqrt(K)
    return


def _error_estimator(dx, phi, mu, sigma, omega, conv, voltages):
    '''Simple error estimator from

        A posteriori error estimation and adaptive mesh-refinement techniques;
        R. Verfürth;
        Journal of Computational and Applied Mathematics;
        Volume 50, Issues 1-3, 20 May 1994, Pages 67-83;
        <https://www.sciencedirect.com/science/article/pii/0377042794902909>.

    The strong PDE is

        - div(1/(mu r) grad(rphi)) + <u, 1/r grad(rphi)> + i sigma omega phi
      = sigma v_k / (2 pi r).
    '''
    from dolfin import cells
    mesh = phi.function_space().mesh()
    # Assemble the cell-wise residual in DG space
    DG = FunctionSpace(mesh, 'DG', 0)
    # get residual in DG
    v = TestFunction(DG)
    R = _residual_strong(dx, v, phi, mu, sigma, omega, conv, voltages)
    r_r = assemble(R[0])
    r_i = assemble(R[1])
    r = r_r * r_r + r_i * r_i
    visualize = True
    if visualize:
        # Plot the cell-wise residual
        u = TrialFunction(DG)
        a = zero() * dx(0)
        subdomain_indices = mu.keys()
        for i in subdomain_indices:
            a += u * v * dx(i)
        A = assemble(a)
        R2 = Function(DG)
        solve(A, R2.vector(), r)
        plot(R2, title='||R||^2')
        interactive()
    K = r.array()
    info('%r' % K)
    h = numpy.array([c.diameter() for c in cells(mesh)])
    eta = h * numpy.sqrt(K)
    return eta


def _residual_strong(dx, v, phi, mu, sigma, omega, conv, voltages):
    '''Get the residual in strong form, projected onto V.
    '''
    r = Expression('x[0]', cell=triangle)
    R = [zero() * dx(0),
         zero() * dx(0)]
    subdomain_indices = mu.keys()
    for i in subdomain_indices:
        # diffusion, reaction
        R_r = - div(1 / (mu[i] * r) * grad(r * phi[0])) \
            - sigma[i] * omega * phi[1]
        R_i = - div(1 / (mu[i] * r) * grad(r * phi[1])) \
            + sigma[i] * omega * phi[0]
        # convection
        if i in conv:
            R_r += dot(conv[i], 1 / r * grad(r * phi[0]))
            R_i += dot(conv[i], 1 / r * grad(r * phi[1]))
        # right-hand side
        if i in voltages:
            R_r -= sigma[i] * voltages[i].real / (2 * pi * r)
            R_i -= sigma[i] * voltages[i].imag / (2 * pi * r)
        R[0] += R_r * v * dx(i)
        R[1] += R_i * v * dx(i)
    return R
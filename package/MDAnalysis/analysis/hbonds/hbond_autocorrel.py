# -*- Mode: python; tab-width: 4; indent-tabs-mode:nil; coding:utf-8 -*-
# vim: tabstop=4 expandtab shiftwidth=4 softtabstop=4 fileencoding=utf-8
#
# MDAnalysis --- http://mdanalysis.googlecode.com
# Copyright (c) 2006-2015 Naveen Michaud-Agrawal, Elizabeth J. Denning, Oliver Beckstein
# and contributors (see AUTHORS for the full list)
#
# Released under the GNU Public Licence, v2 or any higher version
#
# Please cite your use of MDAnalysis in published work:
#
# N. Michaud-Agrawal, E. J. Denning, T. B. Woolf, and O. Beckstein.
# MDAnalysis: A Toolkit for the Analysis of Molecular Dynamics Simulations.
# J. Comput. Chem. 32 (2011), 2319--2327, doi:10.1002/jcc.21787
#
"""
Hydrogen bond autocorrelation --- :mod:`MDAnalysis.analysis.hbonds.hbond_autocorrel`
====================================================================================

:Author: Richard J. Gowers
:Year: 2014
:Copyright: GNU Public License v3

.. versionadded:: 0.9.0

Description
---------------

Calculates the time autocorrelation function, :math:`C_x(t)`, for the hydrogen
bonds in the selections passed to it.  The population of hydrogen bonds at a
given startpoint, :math:`t_0`, is evaluated based on geometric criteria and
then the lifetime of these bonds is monitored over time.  Multiple passes
through the trajectory are used to build an average of the behaviour.

    :math:`C_x(t) = \\left \\langle \\frac{h_{ij}(t_0) h_{ij}(t_0 + t)}{h_{ij}(t_0)^2} \\right\\rangle`

The subscript :math:`x` refers to the definition of lifetime being used, either
continuous or intermittent.  The continuous definition measures the time that
a particular hydrogen bond remains continuously attached, whilst the
intermittent definition allows a bond to break and then subsequently reform and
be counted again.  The relevent lifetime, :math:`\\tau_x`, can then be found
via integration of this function

    :math:`\\tau_x = \\int_0^\\infty C_x(t) dt`

For this, the observed behaviour is fitted to a multi exponential function,
using 2 exponents for the continuous lifetime and 3 for the intermittent
lifetime.

    :math:`C_x(t) = A_1 \\exp( - t / \\tau_1)
    + A_2 \\exp( - t / \\tau_2)
    [+ A_3 \\exp( - t / \\tau_3)]`

Where the final pre expoential factor :math:`A_n` is subject to the condition:

    :math:`A_n = 1 - \\sum\\limits_{i=1}^{n-1} A_i`

.. rubric:: References

.. [notsure]  Multiscale modelling of polymeric systems with hydrogen bonding: Selective removal of degrees of freedom

Input
---------------

Three AtomGroup selections representing the **hydrogens**, **donors** and
**acceptors** that you wish to analyse.  Note that the **hydrogens** and
**donors** selections must be aligned, that is **hydrogens[0]** and
**donors[0]** must represent a bonded pair.  If a single donor therefore has
two hydrogens, it must feature twice in the **donors** AtomGroup.

The keyword **exclusions** allows a tuple of array addresses to be provided,
(Hidx, Aidx),these pairs of hydrogen-acceptor are then not permitted to be
counted as part of the analysis.  This could be used to exclude the
consideration of hydrogen bonds within the same functional group, or to perform
analysis on strictly intermolecular hydrogen bonding.

Hydrogen bonds are defined on the basis of geometric criteria; a
Hydrogen-Acceptor distance of less then **dist_crit** and a
Donor-Hydrogen-Acceptor angle of greater than **angle_crit**.

The length of trajectory to analyse in ps, **sample_time**, is used to choose
what length to analyse.

Multiple passes, controlled by the keyword **nruns**, through the trajectory
are performed and an average calculated.  For each pass, **nsamples** number
of points along the run are calculated.


Output
---------------

All results of the analysis are available through the *solution* attribute.
This is a dictionary with the following keys

- *results*  The raw results of the time autocorrelation function.
- *time*     Time axis, in ps, for the results.
- *fit*      Results of the exponential curve fitting procedure. For the
             *continuous* lifetime these are (A1, tau1, tau2), for the
             *intermittent* lifetime these are (A1, A2, tau1, tau2, tau3).
- *tau*      Calculated time constant from the fit.
- *estimate* Estimated values generated by the calculated fit.

The *results* and *time* values are only filled after the :meth:`run` method,
*fit*, *tau* and *estimate* are filled after the :meth:`solve` method has been
used.


Examples
---------------

::

  from MDAnalysis.analysis import hbonds
  import matplotlib.pyplot as plt
  H = u.selectAtoms('name Hn')
  O = u.selectAtoms('name O')
  N = u.selectAtoms('name N')
  hb_ac = hbonds.HydrogenBondAutoCorrel(u, acceptors = u.atoms.O,
              hydrogens = u.atoms.Hn, donors = u.atoms.N,bond_type='continuous',
              sample_time = 2, nruns = 20, nsamples = 1000)
  hb_ac.run()
  hb_ac.solve()
  tau = hb_ac.solution['tau']
  time = hb_ac.solution['time']
  results = hb_ac.solution['results']
  estimate = hb_ac.solution['estimate']
  plt.plot(time, results, 'ro')
  plt.plot(time, estimate)
  plt.show()


.. autoclass:: HydrogenBondAutoCorrel

   .. automethod:: run

   .. automethod:: solve

   .. automethod:: save_results


"""
import numpy
from numpy import exp
import warnings
from itertools import izip

from MDAnalysis.core.log import ProgressMeter
from MDAnalysis.core.distances import distance_array, calc_angles, calc_bonds


class HydrogenBondAutoCorrel(object):
    """Perform a time autocorrelation of the hydrogen bonds in the system. """

    def __init__(self, universe,
                 hydrogens=None, acceptors=None, donors=None,
                 bond_type=None,
                 exclusions=None,
                 angle_crit=130.0, dist_crit=3.0,  # geometric criteria
                 sample_time=100,  # expected length of the decay in ps
                 time_cut=None,  # cutoff time for intermittent hbonds
                 nruns=1,  # number of times to iterate through the trajectory
                 nsamples=50,  # number of different points to sample in a run
                 pbc=True):
        """
        :Arguments:
          *universe*
            The MDA universe
          *hydrogens*
            Hydrogens which can form hydrogen bonds
          *acceptors*
            Accepting atoms
          *donors*
            The atoms which are connected to the hydrogens
          *bond_type*
            Which definition of hydrogen bond lifetime to consider, either
            'continuous' or 'intermittent'

        :Keywords:
          *exclusions*
            Indices of Hydrogen-Donor pairs to be excluded.  Must be a tuple of
            two arrays
          *angle_crit*
            The angle (in degrees) which all bonds must be greater than [130.0]
          *dist_crit*
            The maximum distance (in Angstroms) for a hydrogen bond [3.0]
          *sample_time*
            The amount of time, in ps, that you wish to observe hydrogen
            bonds for [100]
          *nruns*
            The number of different start points within the trajectory
            to use [1]
          *nsamples*
            Within each run, the number of frames to analyse [50]
          *pbc*
            Whether to consider periodic boundaries in calculations [``True``]
        """
        self.u = universe
        # check that slicing is possible
        try:
            self.u.trajectory[0]
        except:
            raise ValueError("Trajectory must support slicing")

        self.h = hydrogens
        self.a = acceptors
        self.d = donors
        if not (len(self.h) == len(self.a)) and (len(self.a) == len(self.d)):
            raise ValueError("All selections must have the same length")

        self.exclusions = exclusions
        if self.exclusions:
            if not len(self.exclusions[0]) == len(self.exclusions[1]):
                raise ValueError(
                    "'exclusion' must be two arrays of identical length")

        self.bond_type = bond_type
        if self.bond_type not in ['continuous', 'intermittent']:
            raise ValueError(
                "bond_type must be either 'continuous' or 'intermittent'")

        self.a_crit = numpy.deg2rad(angle_crit)
        self.d_crit = dist_crit
        self.pbc = pbc
        self.sample_time = sample_time
        self.nruns = nruns
        self.nsamples = nsamples
        self._slice_traj(sample_time)
        self.time_cut = time_cut

        self.solution = {
            'results': None,  # Raw results
            'time': None,  # Time axis of raw results
            'fit': None,  # coefficients for fit
            'tau': None,  # integral of exponential fit
            'estimate': None  # y values of fit against time
        }

    def _slice_traj(self, sample_time):
        """Set up start and end points in the trajectory for the
        different passes
        """
        dt = self.u.trajectory.dt  # frame step size in time
        req_frames = int(sample_time / dt)  # the number of frames required

        numframes = len(self.u.trajectory)
        if req_frames > numframes:
            warnings.warn("Number of required frames ({}) greater than the"
                          " number of frames in trajectory ({})"
                          .format(req_frames, numframes), RuntimeWarning)

        numruns = self.nruns
        if numruns > numframes:
            numruns = numframes
            warnings.warn("Number of runs ({}) greater than the number of"
                          " frames in trajectory ({})"
                          .format(self.nruns, numframes), RuntimeWarning)

        self._starts = numpy.arange(0, numframes, numframes / numruns, dtype=int)
        # limit stop points using clip
        self._stops = numpy.clip(self._starts + req_frames, 0, numframes)

        self._skip = req_frames / self.nsamples
        if self._skip == 0:  # If nsamples > req_frames
            warnings.warn("Desired number of sample points too high, using {}"
                          .format(req_frames), RuntimeWarning)
            self._skip = 1

    def run(self, force=False):
        """
        Run all the required passes

        :Keywords:
          *force*
            Will overwrite previous results if they exist
        """
        # if results exist, don't waste any time
        if not self.solution['results'] is None and not force:
            return

        master_results = numpy.zeros_like(numpy.arange(self._starts[0],
                                                       self._stops[0],
                                                       self._skip),
                                          dtype=numpy.float32)
        # for normalising later
        counter = numpy.zeros_like(master_results, dtype=numpy.float32)

        pm = ProgressMeter(self.nruns, interval=1,
                           format="Performing run %(step)5d/%(numsteps)d"
                                  "[%(percentage)5.1f%%]\r")

        for i, (start, stop) in enumerate(izip(self._starts, self._stops)):
            pm.echo(i + 1)

            # needed else trj seek thinks a numpy.int64 isn't an int?
            results = self._single_run(int(start), int(stop))

            nresults = len(results)
            if nresults == len(master_results):
                master_results += results
                counter += 1.0
            else:
                master_results[:nresults] += results
                counter[:nresults] += 1.0

        master_results /= counter

        self.solution['time'] = numpy.arange(
            len(master_results),
            dtype=numpy.float32) * self.u.trajectory.dt * self._skip
        self.solution['results'] = master_results

    def _single_run(self, start, stop):
        """Perform a single pass of the trajectory"""
        self.u.trajectory[start]

        # Calculate partners at t=0
        box = self.u.dimensions if self.pbc else None

        # 2d array of all distances
        d = distance_array(self.h.positions, self.a.positions, box=box)
        if self.exclusions:
            # set to above dist crit to exclude
            d[self.exclusions] = self.d_crit + 1.0

        # find which partners satisfy distance criteria
        hidx, aidx = numpy.where(d < self.d_crit)

        a = calc_angles(self.d.positions[hidx], self.h.positions[hidx],
                        self.a.positions[aidx], box=box)
        # from amongst those, who also satisfiess angle crit
        idx2 = numpy.where(a > self.a_crit)
        hidx = hidx[idx2]
        aidx = aidx[idx2]

        nbonds = len(hidx)  # number of hbonds at t=0
        results = numpy.zeros_like(numpy.arange(start, stop, self._skip),
                                   dtype=numpy.float32)

        if self.time_cut:
            # counter for time criteria
            count = numpy.zeros(nbonds, dtype=numpy.float64)

        for i, ts in enumerate(self.u.trajectory[start:stop:self._skip]):
            box = self.u.dimensions if self.pbc else None

            d = calc_bonds(self.h.positions[hidx], self.a.positions[aidx],
                           box=box)
            a = calc_angles(self.d.positions[hidx], self.h.positions[hidx],
                            self.a.positions[aidx], box=box)

            winners = (d < self.d_crit) & (a > self.a_crit)
            results[i] = winners.sum()

            if self.bond_type is 'continuous':
                # Remove losers for continuous definition
                hidx = hidx[numpy.where(winners)]
                aidx = aidx[numpy.where(winners)]
            elif self.bond_type is 'intermittent':
                if self.time_cut:
                    # Add to counter of where losers are
                    count[~ winners] += self._skip * self.u.trajectory.dt
                    count[winners] = 0  # Reset timer for winners

                    # Remove if you've lost too many times
                    # New arrays contain everything but removals
                    hidx = hidx[count < self.time_cut]
                    aidx = aidx[count < self.time_cut]
                    count = count[count < self.time_cut]
                else:
                    pass

            if len(hidx) == 0:  # Once everyone has lost, the fun stops
                break

        results /= nbonds

        return results

    def save_results(self, filename='hbond_autocorrel'):
        """
        Saves the results to a numpy zipped array (.npz, see numpy.savez)

        This can be loaded using numpy.load(filename)

        :Keywords:
          *filename*
            The desired filename [hbond_autocorrel]
        """
        if not self.solution['results'] is None:
            numpy.savez(filename, time=self.solution['time'],
                        results=self.solution['results'])
        else:
            raise ValueError(
                "Results have not been generated, use the run method first")

    def solve(self, p_guess=None):
        """Fit results to an multi exponential decay and integrate to find
        characteristic time

        :Keywords:
          *p_guess*
            Initial guess for the leastsq fit, must match the shape of the
            expected coefficients

        Continuous defition results are fitted to a double exponential,
        intermittent definition are fit to a triple exponential.

        The results of this fitting procedure are saved into the *fit*,
        *tau* and *estimate* keywords in the solution dict.

         - *fit* contains the coefficients, (A1, tau1, tau2) or
           (A1, A2, tau1, tau2, tau3)
         - *tau* contains the calculated lifetime in ps for the hydrogen
           bonding
         - *estimate* contains the estimate provided by the fit of the time
           autocorrelation function

        In addition, the output of the leastsq function is saved into the
        solution dict

         - *infodict*
         - *mesg*
         - *ier*
        """
        from scipy.optimize import leastsq

        if self.solution['results'] is None:
            raise ValueError(
                "Results have not been generated use, the run method first")

        # Prevents an odd bug with leastsq where it expects
        # double precision data sometimes...
        time = self.solution['time'].astype(numpy.float64)
        results = self.solution['results'].astype(numpy.float64)

        def within_bounds(p):
            """Returns True/False if boundary conditions are met or not.
            Uses length of p to detect whether it's handling continuous /
            intermittent

            Boundary conditions are:
             0 < A_x < 1
             sum(A_x) < 1
             0 < tau_x
            """
            if len(p) == 3:
                A1, tau1, tau2 = p
                return (A1 > 0.0) & (A1 < 1.0) & \
                       (tau1 > 0.0) & (tau2 > 0.0)
            elif len(p) == 5:
                A1, A2, tau1, tau2, tau3 = p
                return (A1 > 0.0) & (A1 < 1.0) & (A2 > 0.0) & \
                       (A2 < 1.0) & ((A1 + A2) < 1.0) & \
                       (tau1 > 0.0) & (tau2 > 0.0) & (tau3 > 0.0)

        def err(p, x, y):
            """Custom residual function, returns real residual if all
            boundaries are met, else returns a large number to trick the
            leastsq algorithm
            """
            if within_bounds(p):
                return y - self._my_solve(x, *p)
            else:
                return 100000

        def double(x, A1, tau1, tau2):
            """ Sum of two exponential functions """
            A2 = 1 - A1
            return A1 * exp(-x / tau1) + A2 * exp(-x / tau2)

        def triple(x, A1, A2, tau1, tau2, tau3):
            """ Sum of three exponential functions """
            A3 = 1 - (A1 + A2)
            return A1 * exp(-x / tau1) + A2 * exp(-x / tau2) + A3 * exp(-x / tau3)

        if self.bond_type is 'continuous':
            self._my_solve = double

            if p_guess is None:
                p_guess = (0.5, 10 * self.sample_time, self.sample_time)

            p, cov, infodict, mesg, ier = leastsq(err, p_guess,
                                                  args=(time, results),
                                                  full_output=True)
            self.solution['fit'] = p
            A1, tau1, tau2 = p
            A2 = 1 - A1
            self.solution['tau'] = A1 * tau1 + A2 * tau2
        else:
            self._my_solve = triple

            if p_guess is None:
                p_guess = (0.33, 0.33, 10 * self.sample_time, self.sample_time, 0.1 * self.sample_time)

            p, cov, infodict, mesg, ier = leastsq(err, p_guess,
                                                  args=(time, results),
                                                  full_output=True)
            self.solution['fit'] = p
            A1, A2, tau1, tau2, tau3 = p
            A3 = 1 - A1 - A2
            self.solution['tau'] = A1 * tau1 + A2 * tau2 + A3 * tau3

        self.solution['infodict'] = infodict
        self.solution['mesg'] = mesg
        self.solution['ier'] = ier

        if ier in [1, 2, 3, 4]:  # solution found if ier is one of these values
            self.solution['estimate'] = self._my_solve(
                self.solution['time'], *p)
        else:
            warnings.warn("Solution to results not found", RuntimeWarning)

    def __repr__(self):
        return "< MDAnalysis HydrogenBondAutoCorrel analysis measuring the " + \
               self.bond_type + \
               " lifetime of {0} different hydrogens >".format(len(self.h))

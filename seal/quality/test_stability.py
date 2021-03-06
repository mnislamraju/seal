# -*- coding: utf-8 -*-
"""
Functions for testing and plotting stability of recording across tasks.

@author: David Samu
"""

import warnings

import numpy as np
import scipy as sp
import pandas as pd

from quantities import deg, s

from seal.util import util
from seal.plot import putil, pplot
from seal.analysis import direction


# Stability test constants.
MAX_PD_DIFF = 45 * deg
MAX_BASERATE_DIFF = 10 * 1/s  # maximum baseline rate difference


def get_cross_task_stability_data(UA):
    """Collect unit params for testing stability across tasks."""

    # Collect and save params of interest.
    stim = 'S2'  # testing S2, because S1 location can change btw tasks
    rnames = ('base_rate', 'pref_dir', 'stim_loc')
    tasks = tuple(UA.tasks())
    col = pd.MultiIndex.from_product([tasks, rnames], names=['task', 'res'])
    stab_test = pd.DataFrame(index=UA.uids(), columns=col)
    for uid in UA.uids():
        for task in tasks:
            u = UA.get_unit(uid, task)

            if u.is_empty() or u.is_excluded():
                continue

            if u.DS.empty:
                u.test_DS()

            # Get unit params of interest.
            base_rate = u.get_baseline()
            pref_dir = u.pref_dir(stim, 'max')
            locs = u.TrData[(stim, 'Loc')].unique()

            # Check that only one location has been presented.
            if len(locs) > 1:
                warnings.warn('More than one unique location found' +
                              'for preferred direction for ' + u.Name)
            stab_test.loc[uid, task] = [base_rate, pref_dir, locs]

    UA.StabilityTest = stab_test


def test_cross_task_stability(UA, uid, task1, task2):
    """Test unit stability across tasks."""

    if UA.StabilityTest.empty:
        get_cross_task_stability_data(UA)

    # Test baseline rate change.
    brate1, brate2 = UA.StabilityTest.loc[uid, [(task1, 'base_rate'),
                                                (task2, 'base_rate')]]
    brate_same = abs(brate1 - brate2) < MAX_BASERATE_DIFF

    # Test DS change.
    pdir1, pdir2 = UA.StabilityTest.loc[uid, [(task1, 'pref_dir'),
                                              (task2, 'pref_dir')]]
    pd_same = direction.deg_diff(pdir1, pdir2) < MAX_PD_DIFF

    locs1, locs2 = UA.StabilityTest.loc[uid, [(task1, 'stim_loc'),
                                              (task2, 'stim_loc')]]
    loc_same = np.array_equal(locs1, locs2)

    # Is unit stable between two tasks?
    is_stable = brate_same and (pd_same or not loc_same)

    return is_stable


def rec_stability_test(UA, fname=None, periods=None):
    """Check stability of recording session across tasks."""

    # Init.
    if periods is None:
        periods = ['whole trial', 'fixation']

    # Init figure.
    fig, gsp, axs = putil.get_gs_subplots(nrow=len(periods), ncol=1,
                                          subw=10, subh=2.5, create_axes=True,
                                          as_array=False)

    for prd, ax in zip(periods, axs):

        # Calculate and plot firing rate during given period in each trial
        # across session for all units.
        colors = putil.get_colors()
        task_stats = pd.DataFrame(columns=['t_start', 't_stops', 'label'])
        for task, color in zip(UA.tasks(), colors):

            # Get activity of all units in task.
            tr_rates = []
            for u in UA.iter_thru([task]):
                rates = u.get_prd_rates(prd, tr_time_idx=True)
                tr_rates.append(util.remove_dim_from_series(rates))
            tr_rates = pd.DataFrame(tr_rates)

            # Not (non-empty and included) unit during task.
            if not len(tr_rates.index):
                continue

            # Plot each rate in task.
            tr_times = tr_rates.columns
            pplot.lines(tr_times, tr_rates.T, zorder=1, alpha=0.5,
                        color=color, ax=ax)

            # Plot mean +- sem rate.
            tr_time = tr_rates.columns
            mean_rate, sem_rate = tr_rates.mean(), tr_rates.std()
            lower, upper = mean_rate-sem_rate, mean_rate+sem_rate
            lower[lower < 0] = 0  # remove negative values
            ax.fill_between(tr_time, lower, upper, zorder=2, alpha=.5,
                            facecolor='grey', edgecolor='grey')
            pplot.lines(tr_time, mean_rate, lw=2, color='k', ax=ax)

            # Add task stats.
            task_lbl = '{}, {} units'.format(task, len(tr_rates.index))

            # Add grand mean FR.
            task_lbl += '\nFR: {:.1f} sp/s'.format(tr_rates.mean().mean())

            # Calculate linear trend to test gradual drift.
            slope, _, _, p_value, _ = sp.stats.linregress(tr_times, mean_rate)
            slope = 3600*slope  # convert to change in spike per hour
            pval = util.format_pvalue(p_value, max_digit=3)
            task_lbl += '\n$\delta$FR: {:.1f} sp/s/h'.format(slope)
            task_lbl += '\n{}'.format(pval)

            task_stats.loc[task] = (tr_times.min(), tr_times.max(), task_lbl)

        # Set axes limits.
        tmin, tmax = task_stats.t_start.min(), task_stats.t_stops.max()
        putil.set_limits(ax, xlim=(tmin, tmax))

        # Add task labels after all tasks have been plotted.
        putil.plot_events(task_stats[['t_start', 'label']], y_lbl=0.75,
                          lbl_ha='left', lbl_rotation=0, ax=ax)

        # Format plot.
        xlab = 'Recording time (s)' if prd == periods[-1] else None
        putil.set_labels(ax, xlab=xlab, ylab=prd)
        putil.set_spines(ax, left=False)

    # Save figure.
    title = 'Recording stability of ' + UA.Name
    putil.save_fig(fname, fig, title)

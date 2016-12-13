#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Fri Dec  9 20:31:00 2016

# Functions to plot tuning curve and stimulus selectivity.

@author: David Samu
"""

import numpy as np
from quantities import rad, s

from seal.plot import putil, pplot
from seal.object import constants


def empty_direction_selectivity(fig, outer_gsp):
    """Plot empty direction selectivity plots."""

    mock_gsp_polar = putil.embed_gsp(outer_gsp[0], 1, 1)
    mock_polar_ax = putil.add_mock_axes(fig, mock_gsp_polar[0, 0], polar=True)
    mock_gsp_tuning = putil.embed_gsp(outer_gsp[1], 1, 1)
    mock_tuning_ax = putil.add_mock_axes(fig, mock_gsp_tuning[0, 0])

    return mock_polar_ax, mock_tuning_ax


def direction_selectivity(DSres, title=None, labels=True,
                          polar_legend=True, tuning_legend=True,
                          ffig=None, fig=None, outer_gsp=None):
    """Plot direction selectivity on polar plot and tuning curve."""

    # Init plots.
    if outer_gsp is None:
        fig, outer_gsp, _ = putil.get_gs_subplots(1, 2, subw=6, subh=5,
                                                  create_axes=False, fig=fig)
    ax_polar = fig.add_subplot(outer_gsp[0], polar=True)
    ax_tuning = fig.add_subplot(outer_gsp[1])
    colors = putil.get_colors()
    polar_patches = []
    tuning_patches = []

    for name, DSr in DSres.iterrows():

        # Init stimulus plotting.
        color = next(colors)

        DSI = DSr.DSI.loc['wDS']
        PD = DSr.PD.loc['PD', 'weighted']
        cPD = DSr.PD.loc['cPD', 'weighted']

        # Plot direction selectivity on polar plot.
        ttl = 'Direction response' if labels else None
        polar_dir_resp(DSr.dirs, DSr.meanFR, DSI, PD, title=ttl,
                       color=color, ax=ax_polar)

        # Collect parameters of polar plot (stimulus - response).
        s_pd = str(float(round(PD, 1)))
        s_pd_c = str(int(cPD)) if not np.isnan(float(cPD)) else 'nan'
        lgd_lbl = '{}:   {:.3f}'.format(name, DSI)
        lgd_lbl += '     {:>5}$^\circ$ --> {:>3}$^\circ$ '.format(s_pd, s_pd_c)
        polar_patches.append(putil.get_proxy_artist(lgd_lbl, color))

        # Calculate and plot direction tuning curve.
        xlab = 'Difference from preferred direction (deg)' if labels else None
        ylab = putil.FR_lbl if labels else None
        xticks = [-180, -90, 0, 90, 180]
        xlim = [-180-5, 180+5]  # degrees
        ylim = [0, None]
        ttl = 'Tuning curve' if labels else None
        tuning_curve(DSr.xfit, DSr.yfit, DSr.dirs_cntr, DSr.meanFR_cntr,
                     DSr.semFR_cntr, xticks, xlim, ylim, color, ttl,
                     xlab, ylab, ax=ax_tuning)

        # Collect parameters tuning curve fit.
        a, b, x0, sigma = DSr.fit_params.loc['fit']
        FWHM, R2, RMSE = DSr.fit_res
        s_a, s_b, s_x0, s_sigma, s_FWHM = [str(float(round(p, 1)))
                                           for p in (a, b, x0, sigma, FWHM)]
        s_R2 = format(R2, '.2f')
        lgd_lbl = '{}:{}{:>6}{}{:>6}'.format(name, 5 * ' ', s_a, 5 * ' ', s_b)
        lgd_lbl += '{}{:>6}{}{:>6}'.format(5 * ' ', s_x0, 8 * ' ', s_sigma)
        lgd_lbl += '{}{:>6}{}{:>6}'.format(8 * ' ', s_FWHM, 8 * ' ', s_R2)
        tuning_patches.append(putil.get_proxy_artist(lgd_lbl, color))

    # Add zero reference line to tuning curve.
    putil.add_zero_line('y', ax=ax_tuning)

    # Set super title.
    if title is not None:
        fig.suptitle(title, y=0.98, fontsize='xx-large')

    # Set legends.
    ylegend = -0.38 if labels else -0.15
    fr_on = False if labels else True
    lgd_kws = dict([('fancybox', True), ('shadow', False), ('frameon', fr_on),
                    ('framealpha', 1.0), ('loc', 'lower center'),
                    ('bbox_to_anchor', [0., ylegend, 1., .0]),
                    ('prop', {'family': 'monospace'})])
    polar_lgn_ttl = 'DSI'.rjust(20) + 'PD'.rjust(14) + 'PD8'.rjust(14)
    tuning_lgd_ttl = ('a (sp/s)'.rjust(35) + 'b (sp/s)'.rjust(15) +
                      'x0 (deg)'.rjust(13) + 'sigma (deg)'.rjust(15) +
                      'FWHM (deg)'.rjust(15) + 'R-squared'.rjust(15))

    lgn_params = [(polar_legend, polar_lgn_ttl, polar_patches, ax_polar),
                  (tuning_legend, tuning_lgd_ttl, tuning_patches, ax_tuning)]

    for (plot_legend, lgd_ttl, patches, ax) in lgn_params:
        if not plot_legend:
            continue
        if not labels:  # customisation for summary plot
            lgd_ttl = None
        lgd = putil.set_legend(ax, handles=patches, title=lgd_ttl, **lgd_kws)
        lgd.get_title().set_ha('left')
        if lgd_kws['frameon']:
            lgd.get_frame().set_linewidth(.5)

    # Save figure.
    if hasattr(outer_gsp, 'tight_layout'):
        outer_gsp.tight_layout(fig, rect=[0, 0.0, 1, 0.95])
    putil.save_fig(fig, ffig)


def polar_dir_resp(dirs, resp, DSI=None, PD=None, plot_type='line',
                   complete_missing_dirs=False, color='b', title=None,
                   ffig=None, ax=None):
    """
    Plot response to each directions on polar plot, with a vector pointing to
    preferred direction (PD) with length DSI.
    Use plot_type to change between sector ('bar') and connected ('line') plot
    types.
    """

    # Prepare data.
    # Complete missing directions with 0 response.
    if complete_missing_dirs:
        for i, d in enumerate(constants.all_dirs):
            if d not in dirs:
                dirs = np.insert(dirs, i, d) * dirs.units
                resp = np.insert(resp, i, 0) * 1/s

    rad_dirs = np.array([d.rescale(rad) for d in dirs])

    # Plot response to each directions on polar plot.
    if plot_type == 'bar':  # sector plot
        ndirs = constants.all_dirs.size
        left_rad_dirs = rad_dirs - np.pi/ndirs  # no need for this in MPL 2.0?
        w = 2*np.pi / ndirs                     # same with edgecolor and else?
        ax = pplot.bars(left_rad_dirs, resp, width=w, alpha=0.50, color=color,
                        lw=1, edgecolor='w', title=title, ytitle=1.08,
                        polar=True, ax=ax)
    else:  # line plot
        rad_dirs, resp = [np.append(v, [v[0]]) for v in (rad_dirs, resp)]
        ax = pplot.lines(rad_dirs, resp, color=color,  marker='o', lw=1, ms=4,
                         mew=0, title=title, ytitle=1.08, polar=True, ax=ax)
        ax.fill(rad_dirs, resp, color=color, alpha=0.15)

    # Add arrow representing PD and weighted DSI.
    if DSI is not None and PD is not None:
        rho = np.max(resp) * DSI
        xy = (float(PD.rescale(rad)), rho)
        arr_props = dict(facecolor=color, edgecolor='k', shrink=0.0, alpha=0.5)
        ax.annotate('', xy, xytext=(0, 0), arrowprops=arr_props)


    # Remove spines.
    putil.set_spines(ax, False, False)

    # Save and return plot.
    putil.save_fig(ffig=ffig)
    return ax


def tuning_curve(xfit, yfit, v=None, meanr=None, semr=None, xticks=None,
                 xlim=None, ylim=None, color='b', title=None,
                 xlab=None, ylab=None, ffig=None, ax=None, **kwargs):
    """Plot tuning curve, optionally with data samples."""

    # Plot fitted curve.
    ax = pplot.lines(xfit, yfit, color=color, ax=ax)

    # Plot data samples.
    if meanr is not None and semr is not None:
        pplot.errorbar(v, meanr, yerr=semr, fmt='o', color=color,
                       title=title, xlab=xlab, ylab=ylab, ax=ax, **kwargs)

    # Set x axis ticks.
    if xticks is not None:
        putil.set_xtick_labels(ax, xticks)
    elif v is not None:
        putil.set_xtick_labels(ax, v)

    # Save and return plot.
    putil.save_fig(ffig=ffig)
    return ax
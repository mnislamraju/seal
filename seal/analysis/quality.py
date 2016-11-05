#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Sep  7 16:04:07 2016

Collection of functions related to quality metrics of recording.

@author: David Samu
"""

import numpy as np
import scipy as sp
import pandas as pd
from quantities import s, ms, us, deg
from collections import OrderedDict as OrdDict

from elephant import statistics

from seal.util import plot, util
from seal.object import constants
from seal.object.trials import Trials
from seal.object.periods import Periods


# %% Constants.

REC_GAIN = 4100  # gain of recording
CENSORED_PRD_LEN = 0.675 * ms  # width of censored period
ISI_TH = 1.0 * ms  # ISI violation threshold
WF_T_START = 9  # start INDEX of spiked (aligned by Plexon)
MAX_DRIFT_RATIO = 2  # maximum tolerable drift ratio
MIN_BIN_LEN = 120 * s  # minimum window length for firing binned statistics


# %% Utility functions.

def get_base_data(u):
    """Return base data of unit for quality metrics calculation."""

    # Init values.
    waveforms = u.UnitParams['SpikeWaveforms']
    wavetime = u.UnitParams['WaveformTime']
    spike_dur = u.UnitParams['SpikeDuration']
    spike_times = u.UnitParams['SpikeTimes']
    sampl_per = u.SessParams['SamplPer']

    return waveforms, wavetime, spike_dur, spike_times, sampl_per


def time_bin_data(spike_times, waveforms):
    """Return time binned data for statistics over session time."""

    # Time bins and binned waveforms and spike times.
    t_start, t_stop = spike_times.t_start, spike_times.t_stop
    nbins = max(int(np.floor((t_stop - t_start) / MIN_BIN_LEN)), 1)
    tbin_lims = util.quantity_linspace(t_start, t_stop, s, nbins+1)
    tbins = [(tbin_lims[i], tbin_lims[i+1]) for i in range(len(tbin_lims)-1)]
    tbin_vmid = np.array([np.mean([t1, t2]) for t1, t2 in tbins])*s
    sp_idx_binned = [util.indices_in_window(spike_times, t1, t2)
                     for t1, t2 in tbins]
    wf_binned = [waveforms[sp_idx] for sp_idx in sp_idx_binned]
    sp_times_binned = [spike_times[sp_idx] for sp_idx in sp_idx_binned]

    return tbins, tbin_vmid, wf_binned, sp_times_binned


# %% Core methods .

def waveform_stats(wfs, wtime):
    """Calculates SNR, amplitude and durations of spike waveforms."""

    # No waveforms: waveform stats are uninterpretable.
    if not wfs.size:
        return np.nan, np.array([]) * us, np.array([]) * us

    # SNR: std of mean waveform divided by std of residual waveform (noise).
    wf_mean = np.mean(wfs, 0)
    wf_std = wfs - wf_mean
    snr = np.std(wf_mean) / np.std(wf_std) if wfs.shape[0] > 1 else 10 # single spike

    # Indices of minimum and maximum times.
    # imin = np.argmin(wfs, 1)  # actual minimum of waveform
    imin = wfs.shape[0] * [WF_T_START]  # crossing of threshold (Plexon value)
    imax = [np.argmax(w[imin[i]:]) + imin[i] for i, w in enumerate(wfs)]

    # Duration: time difference between times of minimum and maximum values.
    wf_tmin = wtime[imin]
    wf_tmax = wtime[imax]
    wf_dur = wf_tmax - wf_tmin

    # Amplitude: value difference between mininum and maximum values.
    wmin = wfs[np.arange(len(imin)), imin]
    wmax = wfs[np.arange(len(imax)), imax]
    wf_amp = wmax - wmin

    return snr, wf_amp, wf_dur


def isi_stats(spike_times):
    """Returns ISIs and some related statistics."""

    # No spike: ISI v.r. and TrueSpikes are uninterpretable.
    if not spike_times.size:
        return np.nan, np.nan

    # Only one spike: ISI v.r. is 0%, TrueSpikes is 100%.
    if spike_times.size == 1:
        return 100, 0

    isi = statistics.isi(spike_times).rescale(ms)

    # Percent of spikes violating ISI treshold.
    n_ISI_vr = sum(isi < ISI_TH)
    percent_ISI_vr = 100 * n_ISI_vr / isi.size

    # Percent of spikes estimated to originate from the sorted single unit.
    # Based on refractory period violations.
    # See Hill et al., 2011: Quality Metrics to Accompany Spike Sorting of
    # Extracellular Signals
    N = spike_times.size
    T = (spike_times.t_stop - spike_times.t_start).rescale(ms)
    r = n_ISI_vr
    tmax = ISI_TH
    tmin = CENSORED_PRD_LEN
    tdif = tmax - tmin
    true_spikes = 100*(1/2 + np.sqrt(1/4 - float(r*T / (2*tdif*N**2))))

    return true_spikes, percent_ISI_vr


def classify_unit(snr, true_spikes):
    """Classify unit as single or multi-unit."""

    if true_spikes >= 90 and snr >= 2.0:
        unit_type = 'single unit'
    else:
        unit_type = 'multi unit'

    return unit_type


def test_drift(t, v, tbins, tr_starts, spike_times, do_trial_rejection):
    """Test drift (gradual, or more instantaneous jump or drop) in variable."""

    # Return full task length if not rejecting trials.
    if not do_trial_rejection:
        t1 = spike_times[0]
        t2 = spike_times[-1]
        first_tr_inc = 0
        last_tr_inc = len(tr_starts)
        prd1 = 0
        prd2 = len(tbins)

    else:

        # Number of trials from beginning of session
        # until start and end of each period.
        tr_starts = util.list_to_quantity(tr_starts)
        n_tr_prd_start = [np.sum(util.indices_in_window(tr_starts, vmax=t1))
                          for t1, t2 in tbins]
        n_tr_prd_end = [np.sum(util.indices_in_window(tr_starts, vmax=t2))
                        for t1, t2 in tbins]

        # Find period within acceptible drift range for each bin.
        cols = ['prd_start_i', 'prd_end_i', 'n_prd',
                't_start', 't_end', 't_len',
                'tr_start_i', 'tr_end_i', 'n_tr']
        period_res = pd.DataFrame(index=range(len(v)), columns=cols)
        for i, v1 in enumerate(v):
            vmin, vmax = v1, v1
            for j, v2 in enumerate(v[i:]):
                # Update extreme values.
                vmin = min(vmin, v2)
                vmax = max(vmax, v2)
                # If difference becomes unacceptable, terminate period.
                if vmax > MAX_DRIFT_RATIO*v2 or v2 > MAX_DRIFT_RATIO*vmin:
                    j -= 1
                    break
            end_i = i + j
            period_res.prd_start_i[i] = i
            period_res.prd_end_i[i] = end_i
            period_res.n_prd[i] = j + 1
            period_res.t_start[i] = tbins[i][0]
            period_res.t_end[i] = tbins[end_i][1]
            period_res.t_len[i] = tbins[end_i][1] - tbins[i][0]
            period_res.tr_start_i[i] = n_tr_prd_start[i]
            period_res.tr_end_i[i] = n_tr_prd_end[end_i]
            period_res.n_tr[i] = n_tr_prd_end[end_i] - n_tr_prd_start[i]

        # Find bin with longest period.
        idx = period_res.n_tr.argmax()
        # Indices of longest period.
        prd1 = period_res.prd_start_i[idx]
        prd2 = period_res.prd_end_i[idx]
        # Times of longest period.
        t1 = period_res.t_start[idx]
        t2 = period_res.t_end[idx]
        # Trial indices within longest period.
        first_tr_inc = period_res.tr_start_i[idx]
        last_tr_inc = period_res.tr_end_i[idx] - 1

    # Return included trials and spikes.
    prd_inc = util.indices_in_window(np.array(range(len(tbins))), prd1, prd2)
    tr_inc = util.indices_in_window(np.array(range(len(tr_starts))),
                                    first_tr_inc, last_tr_inc)
    sp_inc = util.indices_in_window(spike_times, t1, t2)

    return t1, t2, prd_inc, tr_inc, sp_inc


# %% Calculate quality metrics, and find trials and units to be excluded.

def test_qm(u, do_trial_rejection=True, ffig_template=None):
    """
    Test ISI, SNR and stationarity of spikes and spike waveforms.
    Optionally find and reject trials with unacceptable
    drift (if do_trial_rejection is True).

    Non-stationarities can happen due to e.g.:
    - electrode drift, or
    - change in the state of the neuron.
    """

    # Init values.
    waveforms, wavetime, spike_dur, spike_times, sampl_per = get_base_data(u)

    # Time binned statistics.
    tbinned_stats = time_bin_data(spike_times, waveforms)
    tbins, tbin_vmid, wf_binned, sp_times_binned = tbinned_stats

    snr_t = [waveform_stats(wfb, wavetime)[0] for wfb in wf_binned]
    rate_t = np.array([spt.size/(t2-t1).rescale(s)
                       for spt, (t1, t2) in zip(sp_times_binned, tbins)]) / s

    # Test drifts and reject trials if necessary.
    tr_starts = u.TrialParams.TrialStart
    test_res = test_drift(tbin_vmid, rate_t, tbins, tr_starts,
                          spike_times, do_trial_rejection)
    t1_inc, t2_inc, prd_inc, tr_inc, sp_inc = test_res

    # Waveform statistics of included spikes only.
    snr, wf_amp, wf_dur = waveform_stats(waveforms[sp_inc], wavetime)

    # Firing rate.
    mean_rate = float(np.sum(sp_inc) / (t2_inc - t1_inc))

    # ISI statistics.
    true_spikes, ISI_vr = isi_stats(spike_times[sp_inc])
    unit_type = classify_unit(snr, true_spikes)

    # Add quality metrics to unit.
    u.QualityMetrics['SNR'] = snr
    u.QualityMetrics['MeanWfAmplitude'] = np.mean(wf_amp)
    u.QualityMetrics['MeanWfDuration'] = np.mean(spike_dur[sp_inc]).rescale(us)
    u.QualityMetrics['MeanFiringRate'] = mean_rate
    u.QualityMetrics['ISIviolation'] = ISI_vr
    u.QualityMetrics['TrueSpikes'] = true_spikes
    u.QualityMetrics['UnitType'] = unit_type

    # Trial removal info.
    tr_exc = np.invert(tr_inc)
    u.QualityMetrics['NTrialsTotal'] = len(tr_starts)
    u.QualityMetrics['NTrialsIncluded'] = np.sum(tr_inc)
    u.QualityMetrics['NTrialsExcluded'] = np.sum(tr_exc)
    u.QualityMetrics['IncludedTrials'] = Trials(tr_inc, 'included trials')
    u.QualityMetrics['ExcludedTrials'] = Trials(tr_exc, 'excluded trials')
    u.QualityMetrics['IncludedSpikes'] = sp_inc

    # Plot quality metric results.
    if ffig_template is not None:
        plot_qm(u, mean_rate, ISI_vr, true_spikes, unit_type, tbin_vmid, tbins,
                snr_t, rate_t, t1_inc, t2_inc, prd_inc, tr_inc, sp_inc,
                ffig_template)

    return u


def test_rejection(u):
    """Check whether unit is to be rejected from analysis."""

    qm = u.QualityMetrics
    exclude = False

    # Insufficient receptive field coverage.
    # TODO: add receptive field coverage information!

    # Extremely low waveform consistency: SNR < 1.0.
    exclude = exclude or qm['SNR'] < 1.0

    # Extremely low unit activity: Firing rate < 1 spikes / second.
    exclude = exclude or qm['MeanFiringRate'] < 1.0

    # Extremely high ISI violation: ISI v.r. > 1%.
    exclude = exclude or qm['ISIviolation'] > 1.0

    # Insufficient number of trials:
    #  # of trials after rejection < 50% of total # of trials.
    exclude = exclude or qm['NTrialsIncluded'] / qm['NTrialsTotal'] < 0.5

    # Insufficient direction selectivity: DSI < 0.1 during both stimulus.
    # DSI is low during remote sample!!!
    ds = u.UnitParams['DirSelectivity'].values()
    exclude = exclude or np.all([dsi < 0.1 for dsi in ds])

    # Preferred direction is not one (with some wide margin)
    # with most activity for either of the stimuli.
    # TODO

    u.QualityMetrics['ExcludeUnit'] = exclude

    return exclude


# %% Plot quality metrics.

def plot_qm(u, mean_rate, ISI_vr, true_spikes, unit_type, tbin_vmid, tbins,
            snr_t, rate_t, t1_inc, t2_inc, prd_inc, tr_inc, sp_inc,
            ffig_template):
    """Plot quality metrics related figures."""

    # Init values.
    waveforms, wavetime, spike_dur, spike_times, sampl_per = get_base_data(u)

    # Get waveform stats of included and excluded spikes.
    wf_inc = waveforms[sp_inc]
    wf_exc = waveforms[np.invert(sp_inc)]
    snr_all, wf_amp_all, wf_dur_all = waveform_stats(waveforms, wavetime)
    snr_inc, wf_amp_inc, wf_dur_inc = waveform_stats(wf_inc, wavetime)
    snr_exc, wf_amp_exc, wf_dur_exc = waveform_stats(wf_exc, wavetime)

    # Minimum and maximum gain.
    gmin = min(-REC_GAIN/2, np.min(waveforms))
    gmax = max(REC_GAIN/2, np.max(waveforms))

    # %% Init plots.

    # Init plotting.
    fig, gsp, sp = plot.get_gs_subplots(nrow=3, ncol=3, subw=4, subh=4)
    ax_wf_inc, ax_wf_exc, ax_filler1 = sp[0, 0], sp[1, 0], sp[2, 0]
    ax_wf_amp, ax_wf_dur, ax_amp_dur = sp[0, 1], sp[1, 1], sp[2, 1]
    ax_snr, ax_rate, ax_filler2 = sp[0, 2], sp[1, 2], sp[2, 2]

    ax_filler1.axis('off')
    ax_filler2.axis('off')

    # Trial markers.
    trial_starts = u.TrialParams.TrialStart
    trms = trial_starts[9::10]
    tr_markers = {tr_i+1: tr_t for tr_i, tr_t in zip(trms.index, trms)}

    # Common variables, limits and labels.
    sp_i = range(-WF_T_START, waveforms.shape[1]-WF_T_START)
    sp_t = sp_i * sampl_per
    ses_t_lim = [min(spike_times.t_start, trial_starts.iloc[0]),
                 max(spike_times.t_stop, trial_starts.iloc[-1])]
    ss = 1.0  # marker size on scatter plot
    sa = .80  # marker alpha on scatter plot
    glim = [gmin, gmax]  # gain axes limit
    wf_t_lim = [min(sp_t), max(sp_t)]
    dur_lim = [0*us, wavetime[-1]-wavetime[WF_T_START]]  # same across units
    amp_lim = [0, gmax-gmin]  # [np.min(wf_ampl), np.max(wf_ampl)]

    tr_alpha = 0.25  # alpha of trial event lines

    # Color spikes by their occurance over session time.
    my_cmap = plot.get_colormap('jet')
    sp_cols = np.tile(np.array([.25, .25, .25, .25]), (len(spike_times), 1))
    if not np.all(np.invert(sp_inc)):  # check if there is any spike included
        sp_t_inc = np.array(spike_times[sp_inc])
        sp_t_inc_shifted = sp_t_inc - sp_t_inc.min()
        sp_cols[sp_inc, :] = my_cmap(sp_t_inc_shifted/sp_t_inc_shifted.max())
    # Put excluded trials to the front, and randomise order of included trials
    # so later spikes don't cover earlier ones.
    sp_order = np.hstack((np.where(np.invert(sp_inc))[0],
                          np.random.permutation(np.where(sp_inc)[0])))

    # Common labels for plots
    wf_t_lab = 'Waveform time ($\mu$s)'
    ses_t_lab = 'Recording time (s)'
    volt_lab = 'Voltage (normalized)'
    amp_lab = 'Amplitude'
    dur_lab = 'Duration ($\mu$s)'

    # %% Waveform shape analysis.

    # Plot excluded and included waveforms on different axes.
    # Color included by occurance in session time to help detect drifts.
    wfs = np.transpose(waveforms)
    for i in sp_order:
        ax = ax_wf_inc if sp_inc[i] else ax_wf_exc
        ax.plot(sp_t, wfs[:, i], color=sp_cols[i, :], alpha=0.05)

    # Format waveform plots
    n_sp_inc, n_sp_exc = sum(sp_inc), sum(np.invert(sp_inc))
    n_tr_inc, n_tr_exc = sum(tr_inc), sum(np.invert(tr_inc))
    for ax, st, n_sp, n_tr in [(ax_wf_inc, 'Included', n_sp_inc, n_tr_inc),
                               (ax_wf_exc, 'Excluded', n_sp_exc, n_tr_exc)]:
        title = '{} waveforms, {} spikes, {} trials'.format(st, n_sp, n_tr)
        plot.set_limits(xlim=wf_t_lim, ylim=glim, ax=ax)
        plot.set_ticks_side(xtick_pos='none', ytick_pos='none', ax=ax)
        plot.show_spines(True, False, False, False, ax=ax)
        plot.set_labels(title=title, xlab=wf_t_lab, ylab=volt_lab, ax=ax)

    # %% Waveform summary metrics.

    # Function to return colors of spikes / waveforms
    # based on whether they are included / excluded.
    def get_color(col_incld, col_bckgrnd='grey'):
        cols = np.array(len(sp_inc) * [col_bckgrnd])
        cols[sp_inc] = col_incld
        return cols

    # Waveform amplitude across session time.
    m_amp, sd_amp = float(np.mean(wf_amp_inc)), float(np.std(wf_amp_inc))
    title = 'Waveform amplitude: {:.1f} $\pm$ {:.1f}'.format(m_amp, sd_amp)
    plot.scatter(spike_times, wf_amp_all, c=get_color('m'), s=ss,
                 xlab=ses_t_lab, ylab=amp_lab, xlim=ses_t_lim, ylim=amp_lim,
                 edgecolors='none', alpha=sa, title=title, ax=ax_wf_amp)

    # Waveform duration across session time.
    wf_dur_all = spike_dur.rescale(us)  # to use TPLCell's waveform duration
    wf_dur_inc = wf_dur_all[sp_inc]
    mdur, sdur = float(np.mean(wf_dur_inc)), float(np.std(wf_dur_inc))
    title = 'Waveform duration: {:.1f} $\pm$ {:.1f} $\mu$s'.format(mdur, sdur)
    plot.scatter(spike_times, wf_dur_all, c=get_color('c'), s=ss,
                 xlab=ses_t_lab, ylab=dur_lab, xlim=ses_t_lim, ylim=dur_lim,
                 edgecolors='none', alpha=sa, title=title, ax=ax_wf_dur)

    # Waveform duration against amplitude.
    title = 'Waveform duration - amplitude'
    plot.scatter(wf_dur_all[sp_order], wf_amp_all[sp_order], c=sp_cols[sp_order],
                 s=ss, xlab=dur_lab, ylab=amp_lab, xlim=dur_lim, ylim=amp_lim,
                 edgecolors='none', alpha=sa, title=title, ax=ax_amp_dur)

    # %% SNR, firing rate and spike timing.

    # Color segments depending on wether they are included / excluded.
    def plot_periods(v, color, ax):
        # Plot line segments.
        for i in range(len(prd_inc[:-1])):
            col = color if prd_inc[i] and prd_inc[i+1] else 'grey'
            x, y = [(tbin_vmid[i], tbin_vmid[i+1]), (v[i], v[i+1])]
            ax.plot(x, y, color=col)
        # Plot line points.
        for i in range(len(prd_inc)):
            col = color if prd_inc[i] else 'grey'
            x, y = [tbin_vmid[i], v[i]]
            ax.plot(x, y, color=col, marker='o',
                    markersize=3, markeredgecolor=col)

    # SNR over session time.
    title = 'SNR: {:.2f}'.format(snr_inc)
    ylim = [0, 1.1*np.max(snr_t)]
    plot_periods(snr_t, 'y', ax_snr)
    plot.lines([], [], c='y', xlim=ses_t_lim, ylim=ylim,
               title=title, xlab=ses_t_lab, ylab='SNR', ax=ax_snr)

    # Firing rate over session time.
    title = 'Firing rate: {:.1f} spike/s'.format(mean_rate)
    ylim = [0, 1.1*np.max(rate_t.magnitude)]
    plot_periods(rate_t, 'b', ax_rate)
    plot.lines([], [], c='b', xlim=ses_t_lim, ylim=ylim,
               title=title, xlab=ses_t_lab, ylab=plot.FR_lbl,
               ax=ax_rate)

    # Add trial markers and highlight included period in each plot.
    for ax in [ax_snr, ax_rate]:

        # Trial markers.
        plot.plot_events(tr_markers, t_unit=s, lw=0.5, ls='--',
                         alpha=tr_alpha, ax=ax)

        # Included period.
        if not np.all(np.invert(tr_inc)):  # check if there is any trials
            incl_segment = Periods([('selected', [t1_inc, t2_inc])])
            plot.plot_segments(incl_segment, t_unit=s, alpha=0.2,
                               color='grey', ymax=0.96, ax=ax)

    # %% Save figure and metrics

    # Create title
    title = ('{}: "{}"'.format(u.Name, unit_type) +
             # '\n\nSNR: {:.2f}%'.format(snr_inc) +
             ',   ISI vr: {:.2f}%'.format(ISI_vr) +
             ',   Tr Sp: {:.1f}%'.format(true_spikes))
    fname = ffig_template.format(u.name_to_fname())
    plot.save_gsp_figure(fig, gsp, fname, title, rect_height=0.92)


# %% Quality tests across tasks.

# TODO: change sectors to lines, add max FR labels, add S2 response to polar?
# Separate S2 axes with for trials grouped by S2Dir!
def direction_response_test(UnitArr, nrate, ftempl, tasks=None,
                            match_FR_scale_across_tasks=True):
    """Plot responses to 8 directions and polar plot in the center."""
    
    # Init plotting.
    if tasks is None:
        tasks = UnitArr.tasks()
    unit_ids = UnitArr.rec_ch_unit_indices()
    ntask = len(tasks)
    nrate = 'R100'

    t1 = -500*ms
    t2 = 3000*ms    
    # TODO: this should be done in plot.raster&rate automatically.
    xticks = np.arange(-1000, 5000+1, 1000)    
    xtck = xticks[np.logical_and(xticks > t1, xticks < t2+1*ms)]
                  
    # Reorder directions to match with order of axes.
    rr_order = [3, 2, 1, 4, 0, 5, 6, 7]
    dir_order = constants.all_dirs[rr_order]
    dir_order = np.insert(dir_order, 4, np.nan) * deg  # add polar plot
                  
    # For each unit over all tasks.
    for uid in unit_ids:
        
        print(uid)
        
        # Init unit and figure.
        Unit_list = UnitArr.unit_list(tasks, [uid], return_empty=True)
        fig, gsp, _ = plot.get_gs_subplots(nrow=1, ncol=ntask,
                                           subw=6, subh=6, create_axes=False)
        rate_axs = []
        polar_axs = []
    
        # Plot direction response of unit in each task.
        for itask, (unit_sps, u) in enumerate(zip(gsp, Unit_list)):
    
            unit_gsp = plot.embed_gsp(unit_sps, 3, 3)
            
            for i, u_gsp in enumerate(unit_gsp):
                
                # Polar plot.
                if i == 4:
                                        
                    ax_polar = fig.add_subplot(u_gsp, polar=True)                    
                    if u.is_empty():
                        ax_polar.axis('off')
                    else:                        
                        dirs, FR, _, _ = u.calc_dir_response('S1', 50*ms, 500*ms)                
                        plot.polar_direction_response(dirs, FR, DSI=None, PD=None,
                                                      color='b', ax=ax_polar)
                    
                        # Remove y-axis ticklabels.
                        plot.hide_ticks(ax_polar, 'y')
                        
                        polar_axs.append(ax_polar)
                  
                # Raster-rate plot.
                else:

                    # Prepare plotting.
                    rr_gsp = plot.embed_gsp(u_gsp, 2, 1)                                                                               
                    if u.is_empty():                                    
                        raster_axs, rate_ax = plot.empty_raster_rate(fig, rr_gsp, 1)
                        
                    # Plot raster&rate plot.
                    else:
                        stim_dir = dir_order[i]
                        dd_trials = u.trials_by_param_values('S1Dir', [stim_dir]) 
                        res = u.plot_raster_rate(nrate, trials=dd_trials, 
                                                 no_labels=True, t1=t1, t2=t2, 
                                                 legend=False, fig=fig, 
                                                 outer_gsp=rr_gsp)
                        _, raster_axs, rate_ax = res
    
                        # Remove y-axis ticklabels from raster plot.
                        [plot.hide_ticks(ax) for ax in raster_axs]
    
                        # Remove axis ticks from rate plot (except first one).
                        if i == 0:
                            plot.set_tick_labels(rate_ax, 'x', pos=xtck, lbls=xtck)
                        else:
                            plot.hide_ticks(rate_ax)

                        rate_axs.append(rate_ax)
                            
                    # Add task name as title.
                    if i == 1:
                        title = tasks[itask]
                        plot.set_labels(title=title, ytitle=1.10, 
                                        ax=raster_axs[0])                        

                    # Match scale of y axes, only within task.
                    if not match_FR_scale_across_tasks:        
                        plot.sync_axes(rate_axs, sync_y=True)
                        rate_axs = []
                        
        # Match scale of y axes across tasks.
        if match_FR_scale_across_tasks:
            plot.sync_axes(rate_axs, sync_y=True)            
            plot.sync_axes(polar_axs, sync_y=True)
        
        # Format and save figure.
        uid_str = util.format_rec_ch_idx(uid)
        title = uid_str.replace('_', ' ')
        fname = ftempl.format(uid_str)
        plot.save_gsp_figure(fig, gsp, fname, title, rect_height=0.92, w_pad=5)


# TODO: update, simplify, refactor, and plot 1 figure per unit over tasks.
def within_trial_unit_test(UnitArr, nrate, fname, plot_info=True,
                           plot_rr=True, plot_ds=True, plot_dd_rr=True):
    """Test unit responses within trails."""

    def n_plots(plot_names):
        return sum([nplots.nrow[pn] * nplots.ncol[pn] for pn in plot_names])

    # Global plotting params.
    row_data = [('info', (1 if plot_info else 0, 1)),
                ('rr', (2 if plot_rr else 0, 1)),
                ('ds', (1, 2 if plot_ds else 0)),
                ('dd_rr', (3 if plot_dd_rr else 0, 2))]
    nplots = util.make_df(row_data, ('nrow', 'ncol'))
    n_unit_subplots = (plot_info + plot_rr + plot_ds + plot_dd_rr)
    n_unit_plots_total = n_plots(nplots.index)

    # Init S1 and S2 queries.
    stim_df = constants.ext_stim_prds.periods()
    stim_df['dir'] = ['S1Dir', 'S2Dir']
    nstim = len(stim_df.index)
    rr_t1 = stim_df.start.min()
    rr_t2 = stim_df.end.max()

    # Init plotting.
    tasks = UnitArr.tasks()
    unit_ids = UnitArr.rec_ch_unit_indices()
    ntask = len(tasks)
    nchunit = len(unit_ids)
    Unit_list = UnitArr.unit_list(tasks, unit_ids, return_empty=True)
    fig, gsp, _ = plot.get_gs_subplots(nrow=nchunit, ncol=ntask,
                                       subw=4.5, subh=7, create_axes=False)
    legend_kwargs = {'borderaxespad': 0}

    # Plot within-trial activity of each unit during each task.
    for unit_sps, u in zip(gsp, Unit_list):

        # Container of axes for given unit.
        unit_gsp = plot.embed_gsp(unit_sps, n_unit_subplots, 1)

        irow = 0  # to keep track of row index
        ds_tested = 'PrefDir' in u.UnitParams

        # Plot unit's info header.
        if plot_info:
            mock_gsp_info = plot.embed_gsp(unit_gsp[irow, 0], 1, 1)
            irow += 1
            if u.is_empty():  # add mock subplot
                plot.add_mock_axes(fig, mock_gsp_info[0, 0])
            else:
                ax = fig.add_subplot(mock_gsp_info[0, 0])
                plot.unit_info(u, ax=ax)

        # Plot raster - rate plot.
        if plot_rr:
            rr_gsp = plot.embed_gsp(unit_gsp[irow, 0], 2, 1)
            irow += 1
            if u.is_empty():  # add mock subplot
                plot.empty_raster_rate(fig, rr_gsp, 1)
            else:
                res = u.plot_raster_rate(nrate, no_labels=True, t1=rr_t1,
                                         t2=rr_t2, legend_kwargs=legend_kwargs,
                                         fig=fig, outer_gsp=rr_gsp)
                fig, raster_axs, rate_ax = res
                plot.replace_tr_num_with_tr_name(raster_axs[0], 'all trials')

        # Plot direction selectivity plot.
        if plot_ds:
            ds_gsp = plot.embed_gsp(unit_gsp[irow, 0], 1, 2)
            irow += 1
            if u.is_empty() or not ds_tested:  # add mock subplot
                plot.empty_direction_selectivity(fig, ds_gsp)
            else:
                u.test_direction_selectivity(no_labels=True, fig=fig,
                                             outer_gsp=ds_gsp)

        # Plot direction selectivity raster - rate plot.
        if plot_dd_rr:
            outer_dd_rr_gsp = plot.embed_gsp(unit_gsp[irow, 0], 1, nstim)
            irow += 1

            for i, (stim, row) in enumerate(stim_df.iterrows()):
                dd_rr_gsp = plot.embed_gsp(outer_dd_rr_gsp[0, i], 2, 1)
                if u.is_empty() or not ds_tested:  # add mock subplot
                    plot.empty_raster_rate(fig, dd_rr_gsp, 2)
                else:
                    dd_trials = u.dir_pref_anti_trials(stim=stim,
                                                       pname=[row.dir],
                                                       comb_values=True)
                    res = u.plot_raster_rate(nrate, trials=dd_trials,
                                             t1=row.start, t2=row.end,
                                             pvals=[0.05], test='t-test',
                                             legend_kwargs=legend_kwargs,
                                             no_labels=True, fig=fig,
                                             outer_gsp=dd_rr_gsp)
                    fig, raster_axs, rate_ax = res

                    # Replace y-axis tickmarks with trial set names.
                    for ax, trs in zip(raster_axs, dd_trials):
                        plot.replace_tr_num_with_tr_name(ax, trs.name)

                    # Hide y-axis tickmarks on second and subsequent rate axes.
                    if i > 0:
                        plot.hide_axes(show_x=True, show_y=False, ax=rate_ax)

    # Match y-axis scales across tasks.
    # List of axes offset lists to match y limit across.
    # Each value indexes a plot within the unit's plot block.
    yplot_idx = (plot_rr * [[n_plots(['info'])+1]] +
                 plot_ds*[[n_plots(['info', 'rr'])],
                          [n_plots(['info', 'rr'])+1]] +
                 plot_dd_rr * [[n_plots(['info', 'rr', 'ds'])+2,
                                n_plots(['info', 'rr', 'ds'])+5]])
    move_sign_lines = (False, False, False, True)
    for offsets, mv_sg_ln in zip(yplot_idx, move_sign_lines):
        for irow in range(nchunit):
            axs = [fig.axes[n_unit_plots_total*ntask*irow +
                            itask*n_unit_plots_total + offset]
                   for offset in offsets
                   for itask in range(ntask)
                   if not Unit_list[irow*ntask + itask].is_empty()]
            plot.sync_axes(axs, sync_y=True)
            if mv_sg_ln:
                [plot.move_significance_lines(ax) for ax in axs]

    # Add unit names to beginning of each row.
    if not plot_info:
        ylab_kwargs = {'rotation': 0, 'size': 'xx-large', 'ha': 'right'}
        offset = 0  # n_plots(['info', 'rr'])
        for irow, unit_id in enumerate(unit_ids):
            ax = fig.axes[n_unit_plots_total*ntask*irow+offset]
            unit_name = 'ch {} / {}'.format(unit_id[1], unit_id[2]) + 15*' '
            plot.set_labels(ylab=unit_name, ax=ax, ylab_kwargs=ylab_kwargs)

    # Add task names to top of each column.
    if not plot_info:
        title_kwargs = {'size': 'xx-large'}
        for icol, task in enumerate(tasks):
            ax = fig.axes[n_unit_plots_total*icol]
            plot.set_labels(title=task, ax=ax, ytitle=1.30,
                            title_kwargs=title_kwargs)

    # Format and save figure.
    title = 'Within trial activity of ' + UnitArr.Name
    plot.save_gsp_figure(fig, gsp, fname, title, rect_height=0.95)


def check_recording_stability(UnitArr, fname):
    """Check stability of recording session across tasks."""

    # Init params.
    periods = constants.tr_prds

    # Init figure.
    fig, gsp, ax_list = plot.get_gs_subplots(nrow=periods.index.size, ncol=1,
                                             subw=10, subh=2.5, as_array=False)

    # Init task info dict.
    tasks = UnitArr.tasks()
    task_stats = OrdDict()
    for task in tasks:
        unit_list = UnitArr.unit_list(tasks=[task])
        task_start = unit_list[0].TrialParams['TrialStart'].iloc[0]
        task_stop = unit_list[0].TrialParams['TrialStop'].iloc[-1]
        task_stats[task] = (len(unit_list), task_start, task_stop)

    unit_ids = UnitArr.rec_ch_unit_indices()
    for (prd_name, (t1, t2)), ax in zip(periods.iterrows(), ax_list):
        # Calculate and plot firing rate during given period within each trial
        # across session for all units.
        all_FR_prd = []
        for unit_id in unit_ids:

            # Get all units (including empty ones for color cycle consistency).
            unit_list = UnitArr.unit_list(ch_unit_idxs=[unit_id],
                                          return_empty=True)
            FR_tr_list = [u.get_rates_by_trial(t1=t1, t2=t2)
                          for u in unit_list]

            # Plot each FRs per task discontinuously.
            colors = plot.get_colors()
            for FR_tr in FR_tr_list:
                color = next(colors)
                if FR_tr is None:  # for color consistency
                    continue
                # For (across-task) continuous plot,
                # need to concatenate across tasks first (see below).
                plot.lines(FR_tr.index, FR_tr, ax=ax, zorder=1,
                           alpha=0.5, color=color)

            # Save FRs for summary plots and stats.
            FR_tr = pd.concat([FR_tr for FR_tr in FR_tr_list])
            all_FR_prd.append(FR_tr)

        # Add mean +- std FR.
        all_FR = pd.concat(all_FR_prd, axis=1)
        tr_time = all_FR.index
        mean_FR = all_FR.mean(axis=1)
        std_FR = all_FR.std(axis=1)
        lower, upper = mean_FR-std_FR, mean_FR+std_FR
        lower[lower < 0] = 0
        ax.fill_between(tr_time, lower, upper, zorder=2,
                        alpha=.75, facecolor='grey', edgecolor='grey')
        plot.lines(tr_time, mean_FR, ax=ax, lw=2, color='k')

        # Add task start marker lines.
        prd_task_stats = OrdDict()
        for task, (n_unit, task_start, task_stop) in task_stats.items():

            # Init.
            tr_idxs = (all_FR.index > task_start) & (all_FR.index <= task_stop)
            meanFR_tr = all_FR.loc[tr_idxs].mean(1)
            task_lbl = '{}\nn = {} units'.format(task, n_unit)

            # Add grand mean FR.
            meanFR = meanFR_tr.mean()
            task_lbl += '\nmean FR = {:.1f} sp/s'.format(meanFR)

            # Calculate linear trend to test gradual drift.
            t, fr = meanFR_tr.index, meanFR_tr
            slope, _, r_value, p_value, _ = sp.stats.linregress(t, fr)
            slope = 3600*slope  # convert to change in spike per hour
            pval = util.format_pvalue(p_value, max_digit=3)
            task_lbl += '\n$\delta$FR = {:.1f} sp/hour ({})'.format(slope,
                                                                    pval)

            prd_task_stats[task_lbl] = task_start

        plot.plot_events(prd_task_stats, t_unit=s, alpha=1.0, color='black',
                         lw=1, linestyle='--', lbl_height=0.75, lbl_ha='left',
                         lbl_rotation=0, ax=ax)

        # Set limits and add labels to plot.
        plot.set_limits(xlim=[None, max(tr_time)], ax=ax)
        plot.set_labels(title=prd_name, xlab='Recording time (s)',
                        ylab=plot.FR_lbl, ax=ax)

    # Format and save figure.
    title = 'Recording stability of ' + UnitArr.Name
    plot.save_gsp_figure(fig, gsp, fname, title, rect_height=0.92)

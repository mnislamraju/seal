#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Functions to calculate and plot quality metrics of units  after spike sorting
(SNR, ISIvr, etc), and to exclude trials / units not meeting QC criteria.

@author: David Samu
"""

import warnings

import numpy as np
import scipy as sp
import pandas as pd
from quantities import s, ms, us

import elephant

from seal.util import util
from seal.plot import putil, pplot, pwaveform


# %% Constants.

# Recording constants.
VMIN = -2048                   # minimum voltage gain of recording
VMAX =  2047                   # maximum voltage gain of recording
CENSORED_PRD_LEN = 0.675*ms    # length of censored period
WF_T_START = 9                 # start index of spikes (aligned by Plexon)

# Constants related to quality metrics calculation.
ISI_TH = 1.0*ms               # ISI violation threshold
MAX_DRIFT_PCT = 200         # maximum tolerable drift percentage (max/min FR)
MIN_BIN_LEN = 120*s           # (minimum) window length for firing binned stats
MIN_TASK_RELATED_DUR = 50*ms  # minimum window length of task related activity

# Constants related to unit exclusion.
min_SNR = 1.0           # min. SNR
min_FR = 1.0            # min. firing rate (sp/s)
max_ISIvr = 1.0         # max. ISI violation ratio (%)
min_n_trs = 20          # min. number of trials (in case monkey quit)
min_inc_trs_ratio = 50  # min. ratio of included trials out of all (%)


# %% Utility functions.

def get_start_stop_times(spk_times, tr_starts, tr_stops):
    """Return start and stop times of recording."""

    tfirst_spk, tlast_spk = spk_times.min()*s, spk_times.max()*s
    tfirst_trl, tlast_trl = tr_starts.min(), tr_stops.max()
    t_start, t_stop = min(tfirst_spk, tfirst_trl), max(tlast_spk, tlast_trl)

    return t_start, t_stop


def time_bin_data(spk_times, waveforms, tr_starts, tr_stops):
    """Return time binned data for statistics over session time."""

    t_start, t_stop = get_start_stop_times(spk_times, tr_starts, tr_stops)

    # Time bins and binned waveforms and spike times.
    nbins = max(int(np.floor((t_stop - t_start) / MIN_BIN_LEN)), 1)
    tbin_lims = util.quantity_linspace(t_start, t_stop, nbins+1, s)
    tbins = [(tbin_lims[i], tbin_lims[i+1]) for i in range(len(tbin_lims)-1)]
    tbin_vmid = np.array([np.mean([t1, t2]) for t1, t2 in tbins])*s
    spk_idx_binned = [util.indices_in_window(spk_times, float(t1), float(t2))
                      for t1, t2 in tbins]
    wf_binned = [waveforms[spk_idx] for spk_idx in spk_idx_binned]
    spk_times_binned = [spk_times[spk_idx] for spk_idx in spk_idx_binned]

    return tbins, tbin_vmid, wf_binned, spk_times_binned


# %% Core methods.

def calc_waveform_stats(waveforms):
    """Calculate waveform duration and amplitude."""

    # Init.
    wfs = np.array(waveforms)
    minV, maxV = wfs.min(), wfs.max()
    step = 1

    # Is waveform set truncated?
    is_truncated = np.sum(wfs == minV) > 1 or np.sum(wfs == maxV) > 1

    # Init waveform data and time vector.
    x = np.array(waveforms.columns)

    def calc_wf_stats(x, y):

        # Remove truncated data points.
        ivalid = (y != minV) & (y != maxV)
        xv, yv = x[ivalid], y[ivalid]

        # Fit cubic spline and get fitted y values.
        tck = sp.interpolate.splrep(xv, yv, s=0)
        xfit = np.arange(x[WF_T_START-2], xv[-1], step)
        yfit = sp.interpolate.splev(xfit, tck)

        # Index of first local minimum and the first following local maximum.
        # If no local min/max is found, then get global min/max.
        imins = sp.signal.argrelextrema(yfit, np.less)[0]
        imin = imins[0] if len(imins) else np.argmin(yfit)
        imaxs = sp.signal.argrelextrema(yfit[imin:], np.greater)[0]
        imax = (imaxs[0] if len(imaxs) else np.argmax(yfit[imin:])) + imin

        # Calculate waveform duration, amplitude and # of valid samples.
        dur = xfit[imax] - xfit[imin] if imin < imax else np.nan
        amp = yfit[imax] - yfit[imin] if imin < imax else np.nan
        nvalid = len(x)

        return dur, amp, nvalid

    # Calculate duration, amplitude and number of valid samples."""
    res = [calc_wf_stats(x, wfs[i, :]) for i in range(wfs.shape[0])]
    wfstats = pd.DataFrame(res, columns=['duration', 'amplitude', 'nvalid'])

    return wfstats, is_truncated, minV, maxV


def isi_stats(spk_times):
    """Returns ISIs and some related statistics."""

    # No spike: ISI v.r. and TrueSpikes are uninterpretable.
    if not spk_times.size:
        return np.nan, np.nan

    # Only one spike: ISI v.r. is 0%, TrueSpikes is 100%.
    if spk_times.size == 1:
        return 100, 0

    isi = elephant.statistics.isi(spk_times).rescale(ms)

    # Percent of spikes violating ISI treshold.
    n_ISI_vr = sum(isi < ISI_TH)
    percent_ISI_vr = 100 * n_ISI_vr / isi.size

    # Percent of spikes estimated to originate from the sorted single unit.
    # Based on refractory period violations.
    # See Hill et al., 2011: Quality Metrics to Accompany Spike Sorting of
    # Extracellular Signals
    N = spk_times.size
    T = (spk_times.max() - spk_times.min()).rescale(ms)
    r = n_ISI_vr
    tmax = ISI_TH
    tmin = CENSORED_PRD_LEN
    tdif = tmax - tmin

    det = 1/4 - float(r*T / (2*tdif*N**2))  # determinant
    true_spikes = 100*(1/2 + np.sqrt(det)) if det >= 0 else np.nan

    return true_spikes, percent_ISI_vr


def calc_snr(waveforms):
    """
    Calculate signal to noise ratio (SNR) of waveforms.

    SNR: std of mean waveform divided by std of residual waveform (noise).
    """

    # Handling extreme case of only a single spike in Unit.
    if waveforms.shape[0] < 2:
        return np.nan

    # Mean, residual and the ratio of their std.
    wf_mean = waveforms.mean()
    wf_res = waveforms - wf_mean
    snr = wf_mean.std() / np.array(wf_res).std()

    return snr


def classify_unit(snr, true_spikes):
    """Classify unit as single or multi-unit."""

    if true_spikes >= 90 and snr >= 2.0:
        unit_type = 'single unit'
    else:
        unit_type = 'multi unit'

    return unit_type


def test_drift(t, v, tbins, tr_starts, spk_times):
    """Test drift (gradual, or more instantaneous jump or drop) in variable."""

    # Number of trials from beginning of session
    # until start and end of each period.
    tr_starts_arr = util.list_to_quantity(tr_starts)
    n_tr_prd_start = [np.sum(tr_starts_arr < t1) for t1, t2 in tbins]
    n_tr_prd_end = [np.sum(tr_starts_arr < t2) for t1, t2 in tbins]

    # Find period within acceptible drift range for each bin.
    cols = ['prd_start_i', 'prd_end_i', 'n_prd',
            't_start', 't_end', 't_len',
            'tr_start_i', 'tr_end_i', 'n_tr']
    prd_res = pd.DataFrame(index=range(len(v)), columns=cols)
    for i, v1 in enumerate(v):
        vmin, vmax = v1, v1
        for j, v2 in enumerate(v[i:]):
            # Update extreme values.
            vmin = min(vmin, v2)
            vmax = max(vmax, v2)
            # If difference becomes unacceptable, terminate period.
            if vmax > MAX_DRIFT_PCT/100*v2 or v2 > MAX_DRIFT_PCT/100*vmin:
                j -= 1
                break
        end_i = i + j
        prd_res.prd_start_i[i] = i
        prd_res.prd_end_i[i] = end_i
        prd_res.n_prd[i] = j + 1
        prd_res.t_start[i] = tbins[i][0]
        prd_res.t_end[i] = tbins[end_i][1]
        prd_res.t_len[i] = tbins[end_i][1] - tbins[i][0]
        prd_res.tr_start_i[i] = n_tr_prd_start[i]
        prd_res.tr_end_i[i] = n_tr_prd_end[end_i]
        prd_res.n_tr[i] = n_tr_prd_end[end_i] - n_tr_prd_start[i]

    # Find bin with longest period.
    idx = prd_res.n_tr.argmax()
    # Indices of longest period.
    prd1 = prd_res.prd_start_i[idx]
    prd2 = prd_res.prd_end_i[idx]
    # Times of longest period.
    t1_inc = prd_res.t_start[idx]
    t2_inc = prd_res.t_end[idx]
    # Trial indices within longest period.
    first_tr = prd_res.tr_start_i[idx]
    last_tr = prd_res.tr_end_i[idx]

    # Return included trials and spikes.
    prd_inc = util.indices_in_window(np.arange(len(tbins)), prd1, prd2)
    tr_inc = (tr_starts.index >= first_tr) & (tr_starts.index < last_tr)
    spk_inc = util.indices_in_window(spk_times, float(t1_inc), float(t2_inc))

    return t1_inc, t2_inc, prd_inc, tr_inc, spk_inc


def set_inc_trials(first_tr, last_tr, tr_starts, tr_stops, spk_times,
                   tbin_vmid):
    """Set included trials by values provided."""

    # Included trial range.
    tr_inc = (tr_starts.index >= first_tr) & (tr_starts.index < last_tr)

    # Start and stop times of included period.
    tstart, tstop = get_start_stop_times(spk_times, tr_starts, tr_stops)
    t1_inc = tstart if first_tr == 0 else tr_starts[first_tr]
    t2_inc = tstop if last_tr == len(tr_starts) else tr_starts[last_tr]

    # Included time periods.
    prd_inc = (tbin_vmid >= t1_inc) & (tbin_vmid <= t2_inc)

    # Included spikes.
    spk_inc = util.indices_in_window(spk_times, t1_inc, t2_inc)

    return t1_inc, t2_inc, prd_inc, tr_inc, spk_inc


def calc_baseline_rate(u):
    """Calculate baseline firing rate of unit."""

    base_rate = u.get_prd_rates('baseline').mean()
    return base_rate


def test_task_relatedness(u):
    """Test if unit has task related activity."""

    # Init.
    prds_to_test = ['S1', 'early delay', 'late delay', 'S2', 'post-S2']
    baseline = util.remove_dim_from_series(u.get_prd_rates('baseline'))
    nrate = u.init_nrate()
    trs = u.inc_trials()
    test = 'wilcoxon'
    p = 0.05
    is_task_related = False

    if not len(trs):
        return False

    # Go through each period to be tested
    for prd in prds_to_test:

        # Get rates during period.
        t1s, t2s = u.pr_times(prd, add_latency=True, concat=False)
        prd_rates = u._Rates[nrate].get_rates(trs, t1s, t2s)

        # Create baseline rate data matrix.
        base_rates = np.tile(baseline, (len(prd_rates.columns), 1)).T
        base_rates = pd.DataFrame(base_rates, index=baseline.index,
                                  columns=prd_rates.columns)

        # Run test at each time sample across period.
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            sign_prds = util.sign_periods(prd_rates, base_rates, p, test,
                                          min_len=MIN_TASK_RELATED_DUR)

        # Check if there's any long-enough significant period.
        if len(sign_prds):
            is_task_related = True
            break

    return is_task_related


# %% Calculate quality metrics, and find trials and units to be excluded.

def test_qm(u, include=None, first_tr=None, last_tr=None):
    """
    Test ISI, SNR and stationarity of FR and spike waveforms.
    Find trials with unacceptable drift.

    Non-stationarities can happen due to e.g.:
    - electrode drift, or
    - change in the state of the neuron.

    Optionally, user can provide whether to include unit and selected trials.
    """

    if u.is_empty():
        return

    # Init values.
    waveforms = u.Waveforms
    spk_times = u.SpikeParams['time']

    # Calculate waveform statistics of each spike.
    wf_stats, is_truncated, minV, maxV = calc_waveform_stats(waveforms)
    u.SpikeParams['duration'] = wf_stats['duration']
    u.SpikeParams['amplitude'] = wf_stats['amplitude']
    u.SpikeParams['nvalid'] = wf_stats['nvalid']
    u.UnitParams['truncated'] = is_truncated
    u.UnitParams['minV'] = min(minV, VMIN)
    u.UnitParams['maxV'] = max(maxV, VMAX)

    tr_starts, tr_stops = u.TrialParams.TrialStart, u.TrialParams.TrialStop

    # Time binned statistics.
    tbinned_stats = time_bin_data(spk_times, waveforms, tr_starts, tr_stops)
    tbins, tbin_vmid, wf_binned, spk_times_binned = tbinned_stats

    rate_t = np.array([spkt.size/(t2-t1).rescale(s)
                       for spkt, (t1, t2) in zip(spk_times_binned, tbins)]) / s

    # Trial exclusion.
    if (first_tr is not None) and (last_tr is not None):
        # Use passed parameters.
        res = set_inc_trials(first_tr, last_tr, tr_starts, tr_stops,
                             spk_times, tbin_vmid)
    else:
        # Test drifts and reject trials if necessary.
        res = test_drift(tbin_vmid, rate_t, tbins, tr_starts, spk_times)
    t1_inc, t2_inc, prd_inc, tr_inc, spk_inc = res

    u.update_included_trials(tr_inc)

    # SNR.
    snr = calc_snr(waveforms[spk_inc])

    # Firing rate.
    mean_rate = np.sum(spk_inc) / float(t2_inc-t1_inc)

    # ISI statistics.
    true_spikes, ISIvr = isi_stats(np.array(spk_times[spk_inc])*s)
    unit_type = classify_unit(snr, true_spikes)

    # Add quality metrics to unit.
    u.QualityMetrics['SNR'] = snr
    u.QualityMetrics['mWfDur'] = u.SpikeParams.duration[spk_inc].mean()
    u.QualityMetrics['mFR'] = mean_rate
    u.QualityMetrics['ISIvr'] = ISIvr
    u.QualityMetrics['TrueSpikes'] = true_spikes
    u.QualityMetrics['UnitType'] = unit_type
    u.QualityMetrics['baseline'] = calc_baseline_rate(u)
    u.QualityMetrics['TaskRelated'] = test_task_relatedness(u)

    # Run unit exclusion test.
    if include is None:
        include = test_rejection(u)
    u.set_excluded(not include)

    # Return all results.
    res = {'tbin_vmid': tbin_vmid, 'rate_t': rate_t,
           't1_inc': t1_inc, 't2_inc': t2_inc, 'prd_inc': prd_inc,
           'tr_inc': tr_inc, 'spk_inc': spk_inc}
    return res


def test_rejection(u):
    """Check whether unit is to be rejected from analysis."""

    qm = u.QualityMetrics
    test_passed = pd.Series()

    # Insufficient receptive field coverage.
    # th_passed.append(qm['RC_coverage'] < min_RF_coverage)

    # Extremely low waveform consistency (SNR).
    test_passed['SNR'] = qm['SNR'] > min_SNR

    # Extremely low unit activity (FR).
    test_passed['FR'] = qm['mFR'] > min_FR

    # Extremely high ISI violation ratio (ISIvr).
    test_passed['ISI'] = qm['ISIvr'] < max_ISIvr

    # Insufficient total number of trials (monkey quit).
    test_passed['NTotalTrs'] = qm['NTrialsTotal'] > min_n_trs

    # Insufficient amount of included trials.
    inc_trs_ratio = 100 * qm['NTrialsInc'] / qm['NTrialsTotal']
    test_passed['IncTrsRatio'] = inc_trs_ratio > min_inc_trs_ratio

    # Not task-related. Criterion to be used later!
    # test_passed['TaskRelated'] = qm['TaskRelated']

    # Include unit if all criteria met.
    include = test_passed.all()

    return include


# %% Plot quality metrics.

def plot_qm(u, tbin_vmid, rate_t, t1_inc, t2_inc, prd_inc, tr_inc, spk_inc,
            add_lbls=False, ftempl=None, fig=None, sps=None):
    """Plot quality metrics related figures."""

    # Init values.
    waveforms = np.array(u.Waveforms)
    wavetime = u.Waveforms.columns * us
    spk_times = np.array(u.SpikeParams['time'], dtype=float)
    mean_rate = u.QualityMetrics['mFR']

    # Minimum and maximum gain.
    gmin = u.UnitParams['minV']
    gmax = u.UnitParams['maxV']

    # %% Init plots.

    # Disable inline plotting to prevent memory leak.
    putil.inline_off()

    # Init figure and gridspec.
    fig = putil.figure(fig)
    if sps is None:
        sps = putil.gridspec(1, 1)[0]
    ogsp = putil.embed_gsp(sps, 2, 1, height_ratios=[0.12, 1])

    info_sps, qm_sps = ogsp[0], ogsp[1]

    # Info header.
    gsp_info = putil.embed_gsp(info_sps, 1, 1)
    info_ax = fig.add_subplot(gsp_info[0, 0])
    putil.unit_info(u, ax=info_ax)

    # Create axes.
    gsp = putil.embed_gsp(qm_sps, 3, 2, wspace=0.3, hspace=0.4)
    ax_wf_inc, ax_wf_exc = [fig.add_subplot(gsp[0, i]) for i in (0, 1)]
    ax_wf_amp, ax_wf_dur = [fig.add_subplot(gsp[1, i]) for i in (0, 1)]
    ax_amp_dur, ax_rate = [fig.add_subplot(gsp[2, i]) for i in (0, 1)]

    # Trial markers.
    trial_starts = u.TrialParams['TrialStart']
    trial_stops = u.TrialParams['TrialStop']
    tr_markers = pd.DataFrame({'time': trial_starts[9::10]})
    tr_markers['label'] = [str(itr+1) if i % 2 else ''
                           for i, itr in enumerate(tr_markers.index)]

    # Common variables, limits and labels.
    spk_t = u.SessParams.sampl_prd * (np.arange(waveforms.shape[1])-WF_T_START)
    ses_t_lim = get_start_stop_times(spk_times, trial_starts, trial_stops)
    ss, sa = 1.0, 0.8  # marker size and alpha on scatter plot

    # Color spikes by their occurance over session time.
    my_cmap = putil.get_cmap('jet')
    spk_cols = np.tile(np.array([.25, .25, .25, .25]), (len(spk_times), 1))
    if np.any(spk_inc):  # check if there is any spike included
        spk_t_inc = np.array(spk_times[spk_inc])
        tmin, tmax = float(spk_times.min()), float(spk_times.max())
        spk_cols[spk_inc, :] = my_cmap((spk_t_inc-tmin) / (tmax-tmin))
    # Put excluded trials to the front, and randomise order of included trials
    # so later spikes don't systematically cover earlier ones.
    spk_order = np.hstack((np.where(np.invert(spk_inc))[0],
                           np.random.permutation(np.where(spk_inc)[0])))

    # Common labels for plots
    ses_t_lab = 'Recording time (s)'

    # %% Waveform shape analysis.

    # Plot included and excluded waveforms on different axes.
    # Color included by occurance in session time to help detect drifts.
    s_waveforms, s_spk_cols = waveforms[spk_order, :], spk_cols[spk_order]
    wf_t_lim, glim = [min(spk_t), max(spk_t)], [gmin, gmax]
    wf_t_lab, volt_lab = 'WF time ($\mu$s)', 'Voltage'
    for st in ('Included', 'Excluded'):
        ax = ax_wf_inc if st == 'Included' else ax_wf_exc
        spk_idx = spk_inc if st == 'Included' else np.invert(spk_inc)
        tr_idx = tr_inc if st == 'Included' else np.invert(tr_inc)

        nspsk, ntrs = sum(spk_idx), sum(tr_idx)
        title = '{} WFs, {} spikes, {} trials'.format(st, nspsk, ntrs)

        # Select waveforms and colors.
        rand_spk_idx = spk_idx[spk_order]
        wfs = s_waveforms[rand_spk_idx, :]
        cols = s_spk_cols[rand_spk_idx]

        # Plot waveforms.
        xlab, ylab = (wf_t_lab, volt_lab) if add_lbls else (None, None)
        pwaveform.wfs(wfs, spk_t, cols=cols, lw=0.1, alpha=0.05,
                      xlim=wf_t_lim, ylim=glim, title=title,
                      xlab=xlab, ylab=ylab, ax=ax)

    # %% Waveform summary metrics.

    # Init data.
    wf_amp_all = u.SpikeParams['amplitude']
    wf_amp_inc = wf_amp_all[spk_inc]
    wf_dur_all = u.SpikeParams['duration']
    wf_dur_inc = wf_dur_all[spk_inc]

    # Set common limits and labels.
    dur_lim = [0, wavetime[-1]-wavetime[WF_T_START]]  # same across units
    glim = max(wf_amp_all.max(), gmax-gmin)
    amp_lim = [0, glim]

    amp_lab = 'Amplitude'
    dur_lab = 'Duration ($\mu$s)'

    # Waveform amplitude across session time.
    m_amp, sd_amp = wf_amp_inc.mean(), wf_amp_inc.std()
    title = 'WF amplitude: {:.1f} $\pm$ {:.1f}'.format(m_amp, sd_amp)
    xlab, ylab = (ses_t_lab, amp_lab) if add_lbls else (None, None)
    pplot.scatter(spk_times, wf_amp_all, spk_inc, c='m', bc='grey', s=ss,
                  xlab=xlab, ylab=ylab, xlim=ses_t_lim, ylim=amp_lim,
                  edgecolors='', alpha=sa, title=title, ax=ax_wf_amp)

    # Waveform duration across session time.
    mdur, sdur = wf_dur_inc.mean(), wf_dur_inc.std()
    title = 'WF duration: {:.1f} $\pm$ {:.1f} $\mu$s'.format(mdur, sdur)
    xlab, ylab = (ses_t_lab, dur_lab) if add_lbls else (None, None)
    pplot.scatter(spk_times, wf_dur_all, spk_inc, c='c', bc='grey', s=ss,
                  xlab=xlab, ylab=ylab, xlim=ses_t_lim, ylim=dur_lim,
                  edgecolors='', alpha=sa, title=title, ax=ax_wf_dur)

    # Waveform duration against amplitude.
    title = 'WF duration - amplitude'
    xlab, ylab = (dur_lab, amp_lab) if add_lbls else (None, None)
    pplot.scatter(wf_dur_all[spk_order], wf_amp_all[spk_order],
                  c=spk_cols[spk_order], s=ss, xlab=xlab, ylab=ylab,
                  xlim=dur_lim, ylim=amp_lim, edgecolors='', alpha=sa,
                  title=title, ax=ax_amp_dur)

    # %% Firing rate.

    # Color segments depending on whether they are included / excluded.
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

    # Firing rate over session time.
    title = 'Firing rate: {:.1f} spike/s'.format(mean_rate)
    xlab, ylab = (ses_t_lab, putil.FR_lbl) if add_lbls else (None, None)
    ylim = [0, 1.25*np.max(rate_t.magnitude)]
    plot_periods(rate_t, 'b', ax_rate)
    pplot.lines([], [], c='b', xlim=ses_t_lim, ylim=ylim, title=title,
                xlab=xlab, ylab=ylab, ax=ax_rate)

    # Trial markers.
    putil.plot_events(tr_markers, lw=0.5, ls='--', alpha=0.35,
                      lbl_height=0.92, ax=ax_rate)

    # Excluded periods.
    excl_prds = []
    tstart, tstop = ses_t_lim
    if tstart != t1_inc:
        excl_prds.append(('beg', tstart, t1_inc))
    if tstop != t2_inc:
        excl_prds.append(('end', t2_inc, tstop))
    putil.plot_periods(excl_prds, ymax=0.92, ax=ax_rate)

    # %% Post-formatting.

    # Maximize number of ticks on recording time axes to prevent covering.
    for ax in (ax_wf_amp, ax_wf_dur, ax_rate):
        putil.set_max_n_ticks(ax, 6, 'x')

    # %% Save figure.
    if ftempl is not None:
        fname = ftempl.format(u.name_to_fname())
        putil.save_gsp_figure(fig, gsp, fname, title, rect_height=0.92)
        putil.inline_on()

    return [ax_wf_inc, ax_wf_exc], ax_wf_amp, ax_wf_dur, ax_amp_dur, ax_rate

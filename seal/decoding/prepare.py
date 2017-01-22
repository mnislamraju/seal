# -*- coding: utf-8 -*-
"""
Functions to prepare data for decoding analysis.

@author: David Samu
"""

import numpy as np
import pandas as pd

import seaborn as sns

from seal.plot import putil
from seal.util import util


# Constants.
min_n_units = 5           # minimum number of units to keep
min_n_trs_per_unit = 10   # minimum number of trials per unit to keep


# %% Unit and trial selection.

def select_units_trials(UA, utids=None, fname=None, do_plotting=True):
    """Select optimal set of units and trials for population decoding."""

    print('Selecting optimal set of units and trials for decoding...')

    # Init.
    if utids is None:
        utids = UA.utids()
    u_rt_grpby = utids.groupby(level=['rec', 'task'])

    # Unit info frame.
    UInfo = pd.DataFrame(index=utids.index)
    UInfo['keep unit'] = False

    # Included trials by unit.
    IncTrs = pd.Series([(UA.get_unit(utid[:3], utid[3]).inc_trials())
                        for utid in utids], index=utids.index)

    # Result DF.
    rec_task = [rt for rt, _ in u_rt_grpby]
    cols = ['units', 'nunits', 'nallunits', 'trials', 'ntrials', 'nalltrials']
    RecInfo = pd.DataFrame(index=rec_task, columns=cols)
    rt_utids = [utids.xs((r, t), level=('rec', 'task')) for r, t in rec_task]
    RecInfo.nallunits = [len(utids) for utids in rt_utids]
    rt_ulist = [UA.get_unit(utids[0][:3], utids[0][3]) for utids in rt_utids]
    RecInfo.nalltrials = [int(u.QualityMetrics['NTrialsTotal'])
                          for u in rt_ulist]

    # Function to plot matrix (DF) included/excluded trials.
    def plot_inc_exc_trials(IncTrsMat, ax, title=None, ytitle=None,
                            xlab='Trial #', ylab=None,):
        # Plot on heatmap.
        sns.heatmap(IncTrsMat, cmap='RdYlGn', center=0.5, cbar=False, ax=ax)
        # Set tick labels.
        putil.hide_tick_marks(ax)
        tr_ticks = [1] + list(np.arange(20, IncTrsMat.shape[1]+1, 20))
        ax.xaxis.set_ticks(tr_ticks)
        ax.set_xticklabels(tr_ticks)
        putil.rot_xtick_labels(ax, 45)
        putil.rot_ytick_labels(ax, 0, va='center')
        putil.set_labels(ax, xlab, ylab, title, ytitle)

    # Init plotting.
    if do_plotting:
        ytitle = 1.40
        fig, gsp, axs = putil.get_gs_subplots(nrow=len(rec_task), ncol=3,
                                              subw=6, subh=4, create_axes=True)

    for i_rt, ((rec, task), rt_utids) in enumerate(u_rt_grpby):
        print(rec, task)

        # Create matrix of included trials of recording & task of units.
        ch_idxs = rt_utids.index.droplevel(3).droplevel(0)
        n_alltrs = RecInfo.nalltrials[(rec, task)]
        IncTrsMat = pd.DataFrame(np.zeros((len(ch_idxs), n_alltrs), dtype=int),
                                 index=ch_idxs, columns=np.arange(n_alltrs)+1)
        for ch_idx, utid in zip(ch_idxs, rt_utids):
            IncTrsMat.loc[ch_idx].iloc[IncTrs[utid]] = 1

        # Plot included/excluded trials after preprocessing.
        if do_plotting:
            ax = axs[i_rt, 0]
            ylab = '{} {}'.format(rec, task)
            title = ('Included (green) and excluded (red) trials'
                     if i_rt == 0 else None)
            plot_inc_exc_trials(IncTrsMat, ax, title, ytitle, ylab=ylab)

        # Calculate and plot overlap of trials across units.
        # How many trials will remain if we iteratively excluding units
        # with the least overlap with the rest of the units?
        def n_cov_trs(df):  # return number of trials covered in df
            return sum(df.all())

        def calc_heuristic(df):
            return df.shape[0] * n_cov_trs(df)

        n_trs = IncTrsMat.sum(1)
        n_units = IncTrsMat.shape[0]

        # Init results DF.
        columns = ('uid', 'ntrs_cov', 'n_rem_u', 'trial x units')
        tr_covs = pd.DataFrame(columns=columns, index=range(n_units+1))
        tr_covs.loc[0] = ('none', n_cov_trs(IncTrsMat), n_units,
                          calc_heuristic(IncTrsMat))

        # Subset of included units (to be updated in each iteration).
        uinc = IncTrsMat.index.to_series()
        for iu in range(1, len(uinc)):

            # Number of covered trials after removing each unit.
            sntrscov = pd.Series([n_cov_trs(IncTrsMat.loc[uinc.drop(uid)])
                                  for uid in uinc], index=uinc.index)

            #########################################
            # Select and remove unit that           #
            # (a) yields maximum trial coverage,    #
            # (b) has minimum number of trials      #
            #########################################
            maxtrscov = sntrscov.max()
            worst_us = sntrscov[sntrscov == maxtrscov].index  # (a)
            utrs = n_trs.loc[worst_us]
            uid_remove = utrs[(utrs == min(utrs))].index[0]   # (b)

            # Update current subset of units and their trial DF.
            uinc.drop(uid_remove, inplace=True)
            tr_covs.loc[iu] = (uid_remove, maxtrscov, len(uinc),
                               calc_heuristic(IncTrsMat.loc[uinc]))

        # Add last unit.
        tr_covs.iloc[-1] = (uinc[0], 0, 0, 0)

        # Plot covered trials against each units removed.
        if do_plotting:
            ax_trc = axs[i_rt, 1]
            sns.tsplot(tr_covs['ntrs_cov'], marker='o', ms=4, color='b',
                       ax=ax_trc)
            title = ('Trials coverage during iterative unit removal'
                     if i_rt == 0 else None)
            xlab, ylab = 'current unit removed', '# trials covered'
            putil.set_labels(ax_trc, xlab, ylab, title, ytitle)
            ax_trc.xaxis.set_ticks(tr_covs.index)
            x_ticklabs = ['none'] + ['{} - {}'.format(ch, ui)
                                     for ch, ui in tr_covs.uid.loc[1:]]
            ax_trc.set_xticklabels(x_ticklabs)
            putil.rot_xtick_labels(ax_trc, 45)
            ax_trc.grid(True)

            # Add # of remaining units to top.
            ax_remu = ax_trc.twiny()
            ax_remu.xaxis.set_ticks(tr_covs.index)
            ax_remu.set_xticklabels(list(range(len(x_ticklabs)))[::-1])
            ax_remu.set_xlabel('# units remaining')
            ax_remu.grid(None)

            # Add heuristic index.
            ax_heur = ax_trc.twinx()
            sns.tsplot(tr_covs['trial x units'], linestyle='--', marker='o',
                       ms=4, color='m',  ax=ax_heur)
            putil.set_labels(ax_heur, ylab='remaining units x covered trials')
            [tl.set_color('m') for tl in ax_heur.get_yticklabels()]
            [tl.set_color('b') for tl in ax_trc.get_yticklabels()]
            ax_heur.grid(None)

        # Decide on which units to exclude.
        min_n_trials = min_n_trs_per_unit * tr_covs['n_rem_u']
        sub_tr_covs = tr_covs[(tr_covs['n_rem_u'] >= min_n_units) &
                              (tr_covs['ntrs_cov'] >= min_n_trials)]

        # If any subset of units passed above criteria.
        rem_uids, exc_uids = pd.Series(), tr_covs.uid[1:]
        n_tr_rem, n_tr_exc = 0, IncTrsMat.shape[1]
        if len(sub_tr_covs.index):
            hmax_idx = sub_tr_covs['trial x units'].argmax()
            rem_uids = tr_covs.uid[(hmax_idx+1):]
            exc_uids = tr_covs.uid[1:hmax_idx+1]
            n_tr_rem = tr_covs.ntrs_cov[hmax_idx]
            n_tr_exc = IncTrsMat.shape[1] - n_tr_rem

            # Add to UnitInfo dataframe
            utids = [(rec, ch, ui, task) for ch, ui in rem_uids]
            UInfo.loc[utids, 'keep unit'] = True

        # Highlight selected point in middle plot.
        if do_plotting:
            sel_seg = [('selection', exc_uids.shape[0]-0.4,
                        exc_uids.shape[0]+0.4)]
            putil.plot_periods(sel_seg, ax=ax_trc)
            [ax.set_xlim([-0.5, n_units+0.5]) for ax in (ax_trc, ax_remu)]

        # Generate remaining trials dataframe.
        RemTrsMat = IncTrsMat.copy().astype(float)
        for exc_uid in exc_uids:   # Remove all trials from excluded units.
            RemTrsMat.loc[exc_uid] = 0.5
        # Remove uncovered trials in remaining units.
        exc_trs = np.where(~RemTrsMat.loc[list(rem_uids)].all())[0]
        if exc_trs.size:
            RemTrsMat.iloc[:, exc_trs] = 0.5
        # Overwrite by trials excluded during preprocessing.
        RemTrsMat[IncTrsMat == False] = 0.0

        # Plot remaining trials.
        if do_plotting:
            ax = axs[i_rt, 2]
            n_u_rem, n_u_exc = len(rem_uids), len(exc_uids)
            title = ('# units remaining: {}, excluded: {}'.format(n_u_rem,
                                                                  n_u_exc) +
                     '\n# trials remaining: {}, excluded: {}'.format(n_tr_rem,
                                                                     n_tr_exc))
            plot_inc_exc_trials(RemTrsMat, ax, title=title, ylab='')

        # Add remaining units and trials to RecInfo.
        rt = (rec, task)
        RecInfo.loc[rt, ('units', 'nunits')] = list(rem_uids), len(rem_uids)
        cov_trs = RemTrsMat.loc[list(rem_uids)].all()
        RecInfo.loc[rt, ('trials', 'ntrials')] = list(cov_trs), sum(cov_trs)

    # Save plot.
    if do_plotting and (fname is not None):
        title = 'Trial & unit selection prior decoding'
        putil.save_gsp_figure(fig, gsp, fname, title, rect_height=0.95,
                              w_pad=3, h_pad=3)

    return RecInfo, UInfo


## %% Direction selectivity analysis.
#
## Consistency of PD across units per recording.
## Do units show consistent PD (maybe +- 45 degrees) on each session?
#
## Determine dominant preferred direction to decode.
#
## Init analysis.
#UnitInfo_GrBy_Rec = UnitInfoDF.groupby(level='rec')
#putil.set_style(seaborn_context, 'darkgrid', rc=rc_args)
#fig, gsp, axs = putil.get_gs_subplots(nrow=len(UnitInfo_GrBy_Rec),
#                                      ncol=len(tasks), subw=6, subh=6,
#                                      ax_kwargs_list={'projection': 'polar'})
#
#xticks = util.deg2rad(constants.all_dirs + 360/8/2*deg)
#
#
## TODO: re-design PDP selection by making the below simpler and better.
#for i_rc, (rec, UInfoRec) in enumerate(UnitInfo_GrBy_Rec):
#    for i_tk, (task, UITRec) in enumerate(UInfoRec.groupby(level='task')):
#        ax = axs[i_rc, i_tk]
#
#        # Init.
#        PD, DSI, uinc = UITRec['PD'], UITRec['DSI'], UITRec['keep unit']
#
#        # Determine direction to decode.
#        incPDdeg, incDSI = np.array(PD[uinc]) * deg, np.array(DSI[uinc])
#        # Calculate unidirectionality index (UD) for each split of units.
#        grp_names = ('grp 1', 'grp 2')
#        tlist = [grp_names, ('UD', 'DPD', 'DPDc', 'nunits', 'cntr_dir')]
#        columns = pd.MultiIndex.from_product(tlist, names=['group', 'metric'])
#        DPDGrpRes = pd.DataFrame(columns=columns, index=constants.all_dirs)
#        for cntr_dir in constants.all_dirs:
#            idx = [util.deg_diff(iPD, cntr_dir) < 90 for iPD in incPDdeg]
#            idx1, idx2 = np.array(idx), np.logical_not(idx)
#            for grp_name, (idx, offset) in zip(grp_names, [(idx1, 0*deg), (idx2, 180*deg)]):
#                ds_grp = util.deg_w_mean(incPDdeg[idx], incDSI[idx], constants.all_dirs)
#                res = list(ds_grp) + [sum(idx), util.deg_mod(cntr_dir+offset)]
#                DPDGrpRes.loc[float(cntr_dir), grp_name] = res
#
#        # Calculate mean DPD (weighted by UD and # of units in each group).
#        def split_quality(row):
#
#            DPDs, cntrDs = row.xs('DPD', level=1), row.xs('cntr_dir', level=1)
#            UDs, nunits = row.xs('UD', level=1), row.xs('nunits', level=1)
#
#            # Flip anti-DPD to it add PDP (not cancel them out).
#            DPDs[1], cntrDs[1] = [util.deg_mod(d+180*deg) for d in [DPDs[1],
#                                                                    cntrDs[1]]]
#
#            # Exclude empty group, if any.
#            non_empty_grps = np.where(nunits > 0)[0]
#            UDs, nunits = UDs[non_empty_grps], nunits[non_empty_grps]
#            DPDs, cntrDs = DPDs[non_empty_grps], cntrDs[non_empty_grps],
#
#            # Get circular mean UD across groups, weighted by
#            # - number of units, and
#            # - difference from central direction.
#            wn_UDs = np.array(nunits*UDs, dtype=float)
#            mUD, mPDP, _ = util.deg_w_mean(DPDs, wn_UDs)
#
#            # Scale (normalised) mean UD by mean of group UDs, weighted by
#            # - number of units, and
#            # - difference of each group's DPD from respective centre.
#            cntr_diff_fac = [float((90*deg - util.deg_diff(DPD, cd)) / (90*deg))
#                             for DPD, cd in zip(DPDs, cntrDs)]
#            weights = cntr_diff_fac * nunits
#            split_qual = np.mean(weights * UDs) * mUD
#            split_qual = np.round(split_qual, 6)  # to prevent rounding errors
#
#            return split_qual
#
#        # Calculate mean Unidirectionality index (split quality) for each split.
#        mUDs = DPDGrpRes.apply(split_quality, axis=1)
#
#        # Find split with maximal UD and higher number of units in 1st group.
#        nunits = DPDGrpRes.loc[mUDs == np.max(mUDs), ('grp 1', 'nunits')]
#        idx = nunits.sort_values(inplace=False).index[-1]
#
#        mUD, DPDc, DADc = mUDs[idx], idx * deg, util.deg_mod((idx+180)*deg)
#        RecInfo.loc[(rec, task), ['DPD', 'DAD', 'UDI']] = DPDc, DADc, mUD
#
#        # Plot PD - DSI on polar plot.
#        title = ('{} {}'.format(rec, task_names[task]) +
#                 '\nMDDs: {}$^\circ$ $\Longleftrightarrow$ {}$^\circ$'.format(int(DPDc),
#                                                             int(DADc)) +
#                 '     UDI: {:.2f}'.format(mUD))
#        plot.scatter(util.deg2rad(PD), DSI, uinc, ylim=[0, 1],
#                     title=title, ytitle=1.10, c='b', ax=ax)
#        ax.set_xticks(xticks, minor=True)
#        ax.grid(b=True, axis='x', which='minor')
#        ax.grid(b=False, axis='x', which='major')
#        # Highlight dominant preferred & antipreferred direction.
#        for D, c in [(DPDc, 'g'), (DADc, 'r')]:
#            hlDs = [util.deg2rad(D+diff) for diff in (45*deg, 0*deg, -45*deg)]
#            for hlD, alpha in [(hlDs, 0.1), ([hlDs[1]], 0.2)]:
#                plot.bars(hlD, len(hlD)*[1], align='center', alpha=alpha,
#                          color=c, ax=ax)
#
## Save plot.
#title = ('Direction selectivity test prior decoding' +
#         '\n\nMDDs: Maximally Discriminable Directions' +
#         '\nUDI: UniDirectionality Index')
#fname = 'results/decoding/preprocessing/DS_test.png'
#plot.save_gsp_figure(fig, gsp, fname, title, rect_height=0.90,
#                     w_pad=0, h_pad=4)
#
#
## %% Trial type distribution analysis.
#
## TODO: Add trial numbers to RecInfo?
#
#cols = ['rec', 'task', 'task_name', 'rec_task']
#RecTrParams = OrdDict()
#for u in UnitArr.unit_list():
#
#    rec, task = u.get_recording_name(), up['task']
#    if (rec, task) not in RecTrParams:
#        RecTrParams[(rec, task)] = u.TrialParams
#
## Total number of trials, correct trials, correct trials per direction, error trials.
#incl_units = UnitInfoDF[UnitInfoDF['keep unit']].index
#IncTrs_GrBy_Rec = IncTrsDF.loc[incl_units].groupby(level='rec')
#
#plot.set_seaborn_style_context('darkgrid', seaborn_context, rc_args)
#
#fig, gsp, axs = plot.get_gs_subplots(nrow=len(IncTrs_GrBy_Rec),
#                                     ncol=len(tasks), subw=8, subh=6)
#
#for i_rc, (rec, ITrRec) in enumerate(IncTrs_GrBy_Rec):
#    for i_tk, (task, ITrRecTask) in enumerate(ITrRec.groupby(level='task')):
#
#        # Get trials covered by select subset of units.
#        IncTrs = pd.DataFrame([iT for idx, iT in ITrRecTask['iT'].iteritems()])
#        cT = IncTrs.all()
#        # Get corresponding trial parameters.
#        cTrParams = RecTrParams[(rec, task)][cT].copy()
#
#        # Add formatted trials params.
#        cTrParams['AnswerCorr'] = 'Error'
#        cTrParams.loc[cTrParams.AnswCorr, 'AnswerCorr'] = 'Correct'
#        cTrParams['S1Direction'] = util.remove_dimension(cTrParams['S1Dir'])
#        cTrParams['S1Direction'] = np.array(cTrParams['S1Direction'],
#                                            dtype=int)
#
#        # Plot as countplot.
#        ax = axs[i_rc, i_tk]
#
#        if not cTrParams.size:
#            ax.axis('off')
#            continue
#
#        sns.countplot(x='S1Direction', hue='AnswerCorr', data=cTrParams,
#                      hue_order=['Correct', 'Error'], ax=ax)
#        sns.despine(ax=ax)
#
#        # Change alpha value of bars depending on DPD - DAD.
#        recinfo = RecInfo.loc[(rec, task)]
#        pa_df = pd.DataFrame({'name': ('pref', 'anti'),
#                              'dir': (recinfo.DPD, recinfo.DAD),
#                              'ls': ('-', '--')},
#                             index=('DPD', 'DAD'))
#        dirs = sorted(cTrParams.S1Direction.unique())
#        alpha = {0: 1, 45: 1, 90: 0.25}
#        for patch in ax.patches:
#
#            # Get orientation of current bar to dominant pref/anti directions.
#            d = dirs[round(patch.get_x())]   # TODO: this is a bit hacky!
#            deg_diff = [int(util.deg_diff(d*deg, PD)) for PD in pa_df.dir]
#
#            # Set alpha of bar.
#            fcol = list(patch.get_facecolor())
#            fcol[3] = alpha[min(deg_diff)]
#            patch.set_facecolor(fcol)
#
#            # Set edge color and width bar.
#            if min(deg_diff) != 90:
#                ls = pa_df.ls[np.argmin(deg_diff)]
#                patch.set_linestyle(ls)
#                patch.set_linewidth(1)
#
#        # Add title.
#        title = '{} {}'.format(rec, task_names[task])
#        nce = cTrParams.AnswerCorr.value_counts()
#        nc, ne = nce.loc['Correct'], nce.loc['Error']
#        pnc, pne = 100*nc/nce.sum(), 100*ne/nce.sum()
#        title += '\n\n# correct: {} ({:.0f}%)'.format(nc, pnc)
#        title += '      # error: {} ({:.0f}%)'.format(ne, pne)
#        plot.set_labels(title=title, xlab='S1 direction', ax=ax)
#
#        # Format legend.
#        if i_rc == 0 and i_tk == 0:
#            # Add pref/anti to legend.
#            extra_artist = [plot.get_proxy_artist(pa_df.name[lbl], color='k',
#                                                  artist_type='line', ls=ls)
#                            for lbl, ls in pa_df.ls.items()]
#            handles = ax.legend().get_patches()
#            handles[0].set_label('Correct')
#            handles[1].set_label('Error')
#            handles = extra_artist + handles
#            ax.legend(title=None, handles=handles, bbox_to_anchor=(0.6, 1),
#                      loc=2, ncol=2, borderaxespad=0., columnspacing=1.5)
#        else:
#            ax.legend_.remove()
#
#
## Save plot.
#title = 'Trial type distribution'
#fname = 'results/decoding/preprocessing/trial_type_distribution.png'
#plot.save_gsp_figure(fig, gsp, fname, title, rect_height=0.95,
#                     w_pad=3, h_pad=3)
#
#
## %% Do decoding.
#
## %autoreload 2
#
#plot.set_seaborn_style_context(seaborn_style, seaborn_context, rc_args)
#
## Init decoding parameters.
#feature_name = 'S1Dir'
#ncv = 10
#offsets = [-45*deg, 0*deg, 45*deg]
#nrate = 'R200'
#t1, t2 = None, 2*s
#sep_err_trs = False
#
#plot_perf = True
#plot_weights = False
#
#off_str = util.format_offsets(offsets)
#print('\n\noffsets: ' + off_str)
#print('\nncv: ' + str(ncv))
#
#if plot_perf:
#    fig_perf, gsp_perf, axs_perf = plot.get_gs_subplots(nrow=len(recordings),
#                                                        ncol=len(tasks),
#                                                        subw=8, subh=6)
#if plot_weights:
#    fig_wght, gsp_wght, axs_wght = plot.get_gs_subplots(nrow=len(recordings),
#                                                        ncol=len(tasks),
#                                                        subw=8, subh=6)
## Init recording, task and params.
#for sep_err_trs in [False]:
#    for nrate in ['R300']:
#        for irec, rec in enumerate(recordings):
#            print('\n'+rec)
#            for itask, task in enumerate(tasks):
#                print('    ' + task)
#                recinfo = RecInfo.loc[(rec, task)]
#
#                # Init units and trials.
#                uidxs = recinfo.units
#                cuidx = [(rec, ic, iu) for ic, iu in uidxs]
#                cov_trs = recinfo.trials
#
#                # Get target direction vector.
#                TrParams = RecTrParams[(rec, task)]
#                feature_vec = TrParams[feature_name]
#                DPD, DAD = recinfo.DPD, recinfo.DAD
#                # Add offsets.
#                DPDs = [util.deg_mod(DPD + offset) for offset in offsets]
#                DADs = [util.deg_mod(DAD + offset) for offset in offsets]
#                # Classify trials.
#                isDPD = pd.Series([d in DPDs for d in feature_vec], dtype=int)
#                isDAD = pd.Series([d in DADs for d in feature_vec], dtype=int)
#                # Check if any clash.
#                if (isDPD & isDAD).any():
#                    warnings.warn('Clashing instances of preferred vs anti classification!')
#                # Create target vector and trial indices.
#                target_vec = isDPD - isDAD
#                dir_trs = target_vec != 0
#
#                # Combine covered and target direction trials.
#                cov_dir_trs = cov_trs & dir_trs
#                target_vec = target_vec[cov_dir_trs]
#
#                # Get 3D FR matrix: time x trial x unit.
#                units = UnitArr.unit_list(tasks=[task], ch_unit_idxs=cuidx)
#                # TODO: update Rates to DataFrame to simplify this.
#                FRs = np.array([u.Rates[nrate].get_rates(cov_dir_trs, t1, t2) for u in units])
#                tvec = np.array(units[0].Rates[nrate].get_times(t1, t2).rescale(ms),dtype=int)
#                MIFR = pd.MultiIndex.from_product((uidxs, cov_dir_trs[cov_dir_trs].index),
#                                                  names=('uidx', 'tridx'))
#                FRdf = pd.DataFrame(FRs.reshape(-1, FRs.shape[2]), index=MIFR, columns=tvec)
#
#                corr_trs = TrParams.AnswCorr[cov_dir_trs] if sep_err_trs else None
#
#                # Run decoding.
#                res = decoding.run_logreg_across_time(FRdf, target_vec, corr_trs, ncv)
#                Perf, Weights, C, ntrg1, ntrg2 = res
#
#                # Plot decoding results.
#                title = ('{} {}'.format(rec, task_names[task]) +
#                 '\n# units: {}'.format(len(cuidx)) +
#                 '     # trials: {} pref / {} anti'.format(ntrg1, ntrg2))
#
#                # Plot prediction accuracy over time.
#                if plot_perf:
#                    ax = axs_perf[irec, itask]
#                    Perf_long = pd.melt(Perf.T, value_vars=list(Perf.index),
#                                        value_name='acc', var_name='time')
#                    Perf_long['icv'] = int(Perf_long.shape[0] / Perf.shape[1]) * list(Perf.columns)
#                    sns.tsplot(Perf_long, time='time', value='acc', unit='icv', ax=ax)
#                    # Add chance level line and stimulus segments.
#                    plot.add_chance_level_line(ax=ax)
#                    plot.plot_segments(constants.stim_prds, t_unit=None, ax=ax)
#                    # Format plot.
#                    plot.set_limits(ylim=[0, 1], ax=ax)
#                    plot.set_labels(xlab=plot.t_lbl, ylab='decoding accuracy',
#                                    title=title, ax=ax)
#                    ax.legend(title=None, bbox_to_anchor=(1., 0),
#                              loc='lower right', borderaxespad=0.)
#
#
#                # TODO: debug this!
#                # Plot unit weights over time.
#                if plot_weights:
#                    ax = axs_wght[irec, itask]
#                    Weights_long = pd.melt(Weights.T, value_vars=list(Weights.index),
#                                           value_name='weight', var_name='time')
#                    Weights_long['cuidx'] = int(Weights_long.shape[0] / Weights.shape[1]) * list(Weights.columns)
#                    sns.tsplot(Weights_long, time='time', value='weight', unit='idx',
#                               condition='cuidx', ax=ax)
#                    # Add chance level line and stimulus segments.
#                    plot.add_chance_level_line(ylevel=0, ax=ax)
#                    plot.plot_segments(constants.stim_prds, t_unit=None, ax=ax)
#                    # Format plot.
#                    plot.set_labels(xlab=plot.t_lbl, ylab='unit weight',
#                                    title=title, ax=ax)
#                    ax.legend(title=None, bbox_to_anchor=(1., 0),
#                              loc='lower right', borderaxespad=0.)
#
#        # Save plots.
#        fname_postfix = '{}_ncv_{}_noffs_{}_w{}_err.png'.format(nrate, ncv, len(offsets),
#                                                                '' if sep_err_trs else 'o')
#        title_postfix = ('\n\nDecoding preferred vs anti {} with offsets: {}'.format(feature_name, off_str) +
#                         '\nFR: {}, error trials {}excluded'.format(nrate, '' if sep_err_trs else 'not ') +
#                         '\nLogistic regression with {}-fold CV'.format(ncv))
#
#        # Performance.
#        if plot_perf:
#            title = 'Prediction accuracy' + title_postfix
#            fname = 'results/decoding/LogRegress/prediction_accuracy/' + fname_postfix
#            plot.save_gsp_figure(fig_perf, gsp_perf, fname, title,
#                                 rect_height=0.88, w_pad=3, h_pad=3)
#
#        # Weights.
#        if plot_weights:
#            title = 'Unit weights' + title_postfix
#            fname = 'results/decoding/LogRegress/unit_weights/' + fname_postfix
#            plot.save_gsp_figure(fig_wght, gsp_wght, fname, title,
#                                 rect_height=0.88, w_pad=3, h_pad=3)

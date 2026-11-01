"""Independent exact source whitelist; no numerical/scoring policy lives here."""

HC_REPLACEMENTS = (
    ('reg batch_aux_valid,aux_match_found;',
     'reg batch_aux_valid,batch_aux_fresh,aux_match_found;'),
    ('batch_aux_valid<=0;aux_match_found<=0;',
     'batch_aux_valid<=0;batch_aux_fresh<=0;aux_match_found<=0;'),
    ("batch_aux_valid<=0;aux_used<=0;fault_events<=fault_events+1'b1;",
     "batch_aux_valid<=0;batch_aux_fresh<=0;aux_used<=0;fault_events<=fault_events+1'b1;"),
    ('batch_window_end<=window_end_sample;batch_aux_valid<=aux_snapshot_valid;',
     'batch_window_end<=window_end_sample;batch_aux_valid<=aux_snapshot_valid;batch_aux_fresh<=aux_age==0;'),
    ('if(aux_match_found)begin chosen_frequency<=af[aux_best_index];aux_used[aux_best_index]<=1;end',
     'if(aux_match_found && (batch_aux_fresh || af[aux_best_index]==frequency_a))begin chosen_frequency<=af[aux_best_index];aux_used[aux_best_index]<=1;end'),
)

HC_ASSERTION = ''' // synthesis translate_off
 // State3 reads H0; auxiliary scan states must retain that synchronous RAM value.
 always @(posedge clk)if(rst_n && state==25 &&
   frequency_a !== frequency_word(metadata[16+bi]))
     $fatal(1,"aux continuation did not compare the retained H0 history");
 // synthesis translate_on
'''


def expected_controller(before, policy):
    if policy == 'fresh_once':
        old = 'batch_aux_valid<=aux_snapshot_valid;'
        assert before.count(old) == 1
        return before.replace(old, 'batch_aux_valid<=aux_snapshot_valid && aux_age==0;')
    if policy == 'fresh_fine_step':
        after = expected_controller(before, 'history_continuation')
        old = ' wire center_change=distance_of(tf[bi],center_hold)>GRID;'
        new = (' wire[31:0] center_distance=distance_of(tf[bi],center_hold);\n'
               ' wire center_change=center_distance>GRID ||\n'
               '   (center_distance==GRID && batch_aux_fresh && aux_match_found && center_hold==chosen_frequency);')
        assert after.count(old) == 1
        return after.replace(old, new)
    assert policy == 'history_continuation'
    after = before
    for old, new in HC_REPLACEMENTS:
        assert after.count(old) == 1, old
        after = after.replace(old, new)
    marker = ' // synthesis translate_off\n integer grid_check;'
    assert after.count(marker) == 1
    return after.replace(marker, HC_ASSERTION + marker)

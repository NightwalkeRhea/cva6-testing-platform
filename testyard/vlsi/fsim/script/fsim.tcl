set_config -global_max_jobs 16 
set_config -fsim_std_args "-fsim=limit+hyperactive+20"
set_config -fsim_mode concurrent
set_config -update_interval 120 ; # in seconds
set_config -enable_intermediate_results 1
## DYNAMIC RUNTIME - Do not modify from this! The __init__.py checks for the args string to parse the required arguments
create_testcases -name {"test1"} \
    -exec ./simv \
    -args ""

report -format standard -campaign  chiptop0 -report pre_fsim_out.rpt -overwrite
report -campaign  chiptop0 -print -summaryonly ;  # Print on STDOUT
# Start fault simulation
fsim -verbose 

# Switch to serial mode for problematic faults in the concurrent mode
set_config -fsim_mode serial
catch {fsim -selected_status {HA IA IF DE DF}}

# Write results report
report -campaign  chiptop0 -print -summaryonly ;  # Print on STDOUT
report -campaign  chiptop0 -summaryonly -report fsim_out_summary.rpt -overwrite
report -format standard -campaign  chiptop0 -report fsim_out.rpt -overwrite
report -campaign  chiptop0 -report fsim_out_hier.rpt -overwrite -hierarchical 100

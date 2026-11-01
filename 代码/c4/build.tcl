set root [file normalize [file dirname [info script]]]
cd $root
open_project pro_v2.gprj
set_option -top_module pro_v2_digital_top_4
set_option -include_path [list [file join $root rtl]]
set_option -verilog_std v2001
set_option -frequency 50
set_option -use_sspi_as_gpio 1
set_option -use_cpu_as_gpio 1
set_option -correct_hold_violation 1
set_option -gen_text_timing_rpt 1
set_option -place_option 3
set_option -route_option 0
set_option -gen_posp 1
set_option -gen_verilog_sim_netlist 1
run syn
run pnr
exit

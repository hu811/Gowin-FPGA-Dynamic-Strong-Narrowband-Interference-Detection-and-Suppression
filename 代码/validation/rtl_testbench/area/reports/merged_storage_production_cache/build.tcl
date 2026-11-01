set root [file normalize [file dirname [info script]]]
cd $root
open_project area.gprj
set_option -top_module area_top
set_option -verilog_std v2001
set_option -frequency 50
set_option -use_sspi_as_gpio 1
set_option -use_cpu_as_gpio 1
set_option -gen_text_timing_rpt 1
run syn
set f [open synthesis_returned.txt w]
puts $f GOWIN_RUN_SYN_RETURNED
close $f
puts GOWIN_RUN_SYN_RETURNED
exit 0

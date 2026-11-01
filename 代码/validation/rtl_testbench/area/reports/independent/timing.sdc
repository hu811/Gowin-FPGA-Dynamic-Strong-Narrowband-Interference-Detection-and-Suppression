create_clock -name clk -period 20.000 [get_ports {clk}]
set_clock_uncertainty -setup 0.200 [get_clocks {clk}]
set_clock_uncertainty -hold 0.200 [get_clocks {clk}]

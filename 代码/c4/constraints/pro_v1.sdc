create_clock -name clk -period 20.000 [get_ports {clk}]
set_clock_uncertainty 0.200 -setup -from [get_clocks {clk}]
set_clock_uncertainty 0.200 -hold -from [get_clocks {clk}]
# Asynchronous reset assertion, synchronous deassertion in reset_sync.
set_false_path -from [get_ports {reset_n}]
set_false_path -from [get_ports {uart_rx_pin}]
set_output_delay -clock clk 5.000 [get_ports {uart_tx_pin led[*]}]

# Asynchronous open-drain inputs enter two-stage synchronizers.
set_false_path -from [get_ports {oled_scl oled_sda}]
set_output_delay -clock clk 5.000 [get_ports {oled_scl oled_sda}]

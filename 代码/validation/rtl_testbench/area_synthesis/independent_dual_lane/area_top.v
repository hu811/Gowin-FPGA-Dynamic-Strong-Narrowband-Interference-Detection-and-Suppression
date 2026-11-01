`timescale 1ns/1ps
module area_top(input clk,rst_n,in_valid,input signed[15:0] in_data,input[31:0] in_seq,
 input fading,new_only,input[5:0] alpha,input[1:0] old_bank,new_bank,
 input coef_we,input[2:0] coef_branch,input[8:0] coef_addr,input signed[17:0] coef_data,
 input build_start,input[1:0] build_bank,input[3:0] build_mask,build_slots,
 input[1:0] diag_select,output ready,out_valid,output signed[15:0] out_data,
 output[31:0] out_seq,output[31:0] diagnostics,output[15:0] max_cycles,
 output busy,build_busy,build_done,build_error);
 wire[31:0] dropped,saturated;
 assign diagnostics=diag_select==0?dropped:diag_select==1?saturated:{16'd0,max_cycles};
dual_lane_correction_mac fir(.clk(clk),.rst_n(rst_n),.in_valid(in_valid),.in_data(in_data),.in_seq(in_seq),
 .fading(fading),.new_only(new_only),.alpha(alpha),.coef_we(coef_we),.coef_branch(coef_branch),.coef_addr(coef_addr),.coef_data(coef_data),
 .ready(ready),.out_valid(out_valid),.out_data(out_data),.out_seq(out_seq),.max_cycles(max_cycles),.busy(busy),.dropped(dropped),.saturated(saturated));
 assign build_busy=1'b0,build_done=build_start,build_error=1'b0;
endmodule

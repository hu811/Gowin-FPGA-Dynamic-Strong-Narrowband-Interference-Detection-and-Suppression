`timescale 1ns/1ps
// Work RAM accesses are serialized by the caller. A write's q is never used;
// the next legal read must complete before q is consumed. Normal-mode SP
// holds q on writes, whereas the former SDP happened to read address0 then.
module backend_work_sp #(parameter WIDTH=24)(input clk,input we,
 input[8:0] write_address,read_address,input[WIDTH-1:0] data,output[WIDTH-1:0] q);
 wire[8:0] address=we ? write_address:read_address;
 wire[31:0] packed_q;
 assign q=packed_q[WIDTH-1:0];
 SP #(.READ_MODE(1'b0),.WRITE_MODE(2'b00),.BIT_WIDTH(32),
 .BLK_SEL(3'b000),.RESET_MODE("SYNC")) ram(
 .DO(packed_q),.DI({{(32-WIDTH){1'b0}},data}),.AD({address,1'b0,4'b1111}),
 .BLKSEL(3'b000),.WRE(we),.CLK(clk),.CE(1'b1),.OCE(1'b1),.RESET(1'b0));
endmodule

`timescale 1ns/1ps
// Direct primitive mapping for the digital-link single physical RAM.
// The inferred two-port form became registers in Gowin; this fixes its mapping.
// 1024 x 16, two independently enabled synchronous ports; write has priority.
module digital_link_ram(
 input clk,input write_a,input read_a,input[9:0] address_a,input[15:0] data_a,
 input write_b,input read_b,input[9:0] address_b,input[15:0] data_b,
 output[15:0] q_a,output[15:0] q_b);
 DPB #(.READ_MODE0(1'b0),.READ_MODE1(1'b0),
   .WRITE_MODE0(2'b00),.WRITE_MODE1(2'b00),
   .BIT_WIDTH_0(16),.BIT_WIDTH_1(16),
   .BLK_SEL_0(3'b000),.BLK_SEL_1(3'b000),.RESET_MODE("SYNC")) ram(
     .DOA(q_a),.DOB(q_b),.DIA(data_a),.DIB(data_b),
     .BLKSELA(3'b000),.BLKSELB(3'b000),
     .ADA({address_a,4'b0011}),.ADB({address_b,4'b0011}),
     .WREA(write_a),.WREB(write_b),.CLKA(clk),.CLKB(clk),
     .CEA(write_a||read_a),.CEB(write_b||read_b),
     .OCEA(1'b1),.OCEB(1'b1),.RESETA(1'b0),.RESETB(1'b0));
endmodule

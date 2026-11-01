`timescale 1ns/1ps
// Exact, unregistered transport arithmetic. These use otherwise spare DSPs;
// no detector/FIR operation, precision, publication time or sample cadence changes.
// Positive uint32 operands need 33 output bits. The DSP adds a[25:0]+b;
// seven fabric bits restore a[31:26], including the full uint32 carry.
module digital_link_dsp_add33(input[31:0] a,b,output[32:0] q);
 wire[47:0] sum;
 MULTALU27X18 #(.P_SEL(1'b0),.A_SEL(1'b0),.C_SEL(1'b1),
   .CASI_SEL(1'b0),.ACC_SEL(1'b0),.PRE_LOAD(48'd0)) unit(
   .DOUT(sum),.CASO(),.SOA(),.A({1'b0,a[25:0]}),.B(18'd1),.C({16'd0,b}),.D(26'd0),
   .SIA(27'd0),.CASI(48'd0),.PSEL(1'b0),.ASEL(1'b0),.CSEL(1'b1),
   .CASISEL(1'b0),.PADDSUB(1'b0),.ACCSEL(1'b0),.ADDSUB(2'd0),
   .CLK(2'd0),.CE(2'b11),.RESET(2'd0));
 wire[6:0] high=sum[32:26]+{1'b0,a[31:26]};
 assign q={high,sum[25:0]};
endmodule

// Exact uint32 magnitude comparison. DSP computes a-b[25:0]; its signed
// upper seven bits are -1..63. Subtracting zero-extended b[31:26] gives
// -64..63, which fits signed seven bits. Its sign is exactly (a < b).
module digital_link_dsp_less32(input[31:0] a,b,output less);
 wire[47:0] difference;
 MULTALU27X18 #(.P_SEL(1'b0),.A_SEL(1'b0),.C_SEL(1'b1),
   .ADD_SUB_0(1'b1),.ADD_SUB_1(1'b0),
   .CASI_SEL(1'b0),.ACC_SEL(1'b0),.PRE_LOAD(48'd0)) unit(
   .DOUT(difference),.CASO(),.SOA(),.A({1'b0,b[25:0]}),.B(18'd1),.C({16'd0,a}),.D(26'd0),
   .SIA(27'd0),.CASI(48'd0),.PSEL(1'b0),.ASEL(1'b0),.CSEL(1'b1),
   .CASISEL(1'b0),.PADDSUB(1'b0),.ACCSEL(1'b0),.ADDSUB(2'b01),
   .CLK(2'd0),.CE(2'b11),.RESET(2'd0));
 wire signed[6:0] high=$signed(difference[32:26])-$signed({1'b0,b[31:26]});
 assign less=high[6];
endmodule

// Three independent modular additions in one 48-bit DSP adder. Zero guard
// bits 10 and 21 absorb carries from the two 10-bit lanes. Even 1023+1023
// is <=2046 (11 bits), so neither lane can carry into the next lane.
module digital_link_dsp_packed_levels #(parameter STATUS_WIDTH=18)(
 input[9:0] input_level,output_level,input_step,output_step,
 input[STATUS_WIDTH-1:0] status_timer,
 output[9:0] input_next,output_next,output[STATUS_WIDTH-1:0] status_next);
 wire[47:0] sum;
 wire[26:0] addend={4'd0,1'b1,1'b0,output_step,1'b0,input_step};
 wire[47:0] state_value={{(26-STATUS_WIDTH){1'b0}},status_timer,1'b0,output_level,1'b0,input_level};
 MULTALU27X18 #(.P_SEL(1'b0),.A_SEL(1'b0),.C_SEL(1'b1),
   .CASI_SEL(1'b0),.ACC_SEL(1'b0),.PRE_LOAD(48'd0)) unit(
   .DOUT(sum),.CASO(),.SOA(),.A(addend),.B(18'd1),.C(state_value),.D(26'd0),
   .SIA(27'd0),.CASI(48'd0),.PSEL(1'b0),.ASEL(1'b0),.CSEL(1'b1),
   .CASISEL(1'b0),.PADDSUB(1'b0),.ACCSEL(1'b0),.ADDSUB(2'd0),
   .CLK(2'd0),.CE(2'b11),.RESET(2'd0));
 assign input_next=sum[9:0];
 assign output_next=sum[20:11];
 assign status_next=sum[22+:STATUS_WIDTH];
endmodule

// During in_valid, consumed has already advanced, so the presented sample's
// sequence is consumed-1. A=-1, B=1, C=zero-extended uint32 performs an exact
// modulo-2^32 decrement without a second 32-bit sequence holding register.
module digital_link_dsp_minus1(input[31:0] a,output[31:0] q);
 wire[47:0] sum;
 MULTALU27X18 #(.P_SEL(1'b0),.A_SEL(1'b0),.C_SEL(1'b1),
   .CASI_SEL(1'b0),.ACC_SEL(1'b0),.PRE_LOAD(48'd0)) unit(
   .DOUT(sum),.CASO(),.SOA(),.A(27'h7ffffff),.B(18'd1),.C({16'd0,a}),.D(26'd0),
   .SIA(27'd0),.CASI(48'd0),.PSEL(1'b0),.ASEL(1'b0),.CSEL(1'b1),
   .CASISEL(1'b0),.PADDSUB(1'b0),.ACCSEL(1'b0),.ADDSUB(2'd0),
   .CLK(2'd0),.CE(2'b11),.RESET(2'd0));
 assign q=sum[31:0];
endmodule

// Exact modular addition for <=26-bit transport registers. A negative step
// is supplied in WIDTH-bit two's complement; truncating the positive DSP sum
// to WIDTH preserves precisely the original register's modulo arithmetic.
module digital_link_dsp_small_add #(parameter WIDTH=18)(
 input[WIDTH-1:0] a,b,output[WIDTH-1:0] q);
 wire[47:0] sum;
 MULTALU27X18 #(.P_SEL(1'b0),.A_SEL(1'b0),.C_SEL(1'b1),
   .CASI_SEL(1'b0),.ACC_SEL(1'b0),.PRE_LOAD(48'd0)) unit(
   .DOUT(sum),.CASO(),.SOA(),.A({{(27-WIDTH){1'b0}},a}),.B(18'd1),
   .C({{(48-WIDTH){1'b0}},b}),.D(26'd0),
   .SIA(27'd0),.CASI(48'd0),.PSEL(1'b0),.ASEL(1'b0),.CSEL(1'b1),
   .CASISEL(1'b0),.PADDSUB(1'b0),.ACCSEL(1'b0),.ADDSUB(2'd0),
   .CLK(2'd0),.CE(2'b11),.RESET(2'd0));
 assign q=sum[WIDTH-1:0];
endmodule

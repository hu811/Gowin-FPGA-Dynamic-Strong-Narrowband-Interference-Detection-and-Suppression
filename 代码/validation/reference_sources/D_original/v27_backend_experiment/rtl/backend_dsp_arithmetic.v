`timescale 1ns/1ps
// Exact modular arithmetic, unregistered native DSP. No schedule changes.
module v27_dsp_add_mod #(parameter WIDTH=26)(input[WIDTH-1:0] a,b,output[WIDTH-1:0] q);
 wire[47:0] p;
 MULTALU27X18 #(.P_SEL(1'b0),.A_SEL(1'b0),.C_SEL(1'b1),.CASI_SEL(1'b0),.ACC_SEL(1'b0),.PRE_LOAD(48'd0)) unit(
 .DOUT(p),.CASO(),.SOA(),.A({{(27-WIDTH){1'b0}},a}),.B(18'd1),.C({{(48-WIDTH){1'b0}},b}),.D(26'd0),
 .SIA(27'd0),.CASI(48'd0),.PSEL(1'b0),.ASEL(1'b0),.CSEL(1'b1),.CASISEL(1'b0),.PADDSUB(1'b0),.ACCSEL(1'b0),.ADDSUB(2'd0),
 .CLK(2'd0),.CE(2'b11),.RESET(2'd0));
 assign q=p[WIDTH-1:0];
endmodule
module v27_dsp_sub_mod #(parameter WIDTH=24)(input[WIDTH-1:0] a,b,output[WIDTH-1:0] q);
 wire[47:0] p;
 MULTALU27X18 #(.P_SEL(1'b0),.A_SEL(1'b0),.C_SEL(1'b1),.ADD_SUB_0(1'b1),.ADD_SUB_1(1'b0),.CASI_SEL(1'b0),.ACC_SEL(1'b0),.PRE_LOAD(48'd0)) unit(
 .DOUT(p),.CASO(),.SOA(),.A({{(27-WIDTH){1'b0}},b}),.B(18'd1),.C({{(48-WIDTH){1'b0}},a}),.D(26'd0),
 .SIA(27'd0),.CASI(48'd0),.PSEL(1'b0),.ASEL(1'b0),.CSEL(1'b1),.CASISEL(1'b0),.PADDSUB(1'b0),.ACCSEL(1'b0),.ADDSUB(2'b01),
 .CLK(2'd0),.CE(2'b11),.RESET(2'd0));
 assign q=p[WIDTH-1:0];
endmodule
module v27_dsp_inc32(input[31:0] a,output[31:0] q);
 wire[47:0] p;
 MULTALU27X18 #(.P_SEL(1'b0),.A_SEL(1'b0),.C_SEL(1'b1),.CASI_SEL(1'b0),.ACC_SEL(1'b0),.PRE_LOAD(48'd0)) unit(
 .DOUT(p),.CASO(),.SOA(),.A(27'd1),.B(18'd1),.C({16'd0,a}),.D(26'd0),
 .SIA(27'd0),.CASI(48'd0),.PSEL(1'b0),.ASEL(1'b0),.CSEL(1'b1),.CASISEL(1'b0),.PADDSUB(1'b0),.ACCSEL(1'b0),.ADDSUB(2'd0),
 .CLK(2'd0),.CE(2'b11),.RESET(2'd0));
 assign q=p[31:0];
endmodule
module v27_dsp_add43(input[42:0] a,b,output[42:0] q);
 wire[47:0] p;
 MULTALU27X18 #(.P_SEL(1'b0),.A_SEL(1'b0),.C_SEL(1'b1),.CASI_SEL(1'b0),.ACC_SEL(1'b0),.PRE_LOAD(48'd0)) unit(
 .DOUT(p),.CASO(),.SOA(),.A({1'b0,a[25:0]}),.B(18'd1),.C({5'd0,b}),.D(26'd0),
 .SIA(27'd0),.CASI(48'd0),.PSEL(1'b0),.ASEL(1'b0),.CSEL(1'b1),.CASISEL(1'b0),.PADDSUB(1'b0),.ACCSEL(1'b0),.ADDSUB(2'd0),
 .CLK(2'd0),.CE(2'b11),.RESET(2'd0));
 wire[16:0] high=p[42:26]+a[42:26];
 assign q={high,p[25:0]};
endmodule

`timescale 1ns/1ps
// Official GW5A ALU ADD mode. Initial CIN=0; the variable increment enters
// bit0 I1. All other I1 are0, with COUT using the dedicated carry connection.
module v31_rne31_alu34(input signed[63:0] value,output signed[63:0] rounded);
 wire[33:0] quotient={value[63],value[63:31]};
 wire increment=value[30] && ((|value[29:0]) || value[31]);
 wire[34:0] carry;wire[33:0] word;
 assign carry[0]=1'b0;
 genvar k;
 generate for(k=0;k<34;k=k+1)begin:round_carry
   ALU #(.ALU_MODE(0)) addbit(.SUM(word[k]),.COUT(carry[k+1]),.I0(quotient[k]),
     .I1(k==0 ? increment:1'b0),.I3(1'b0),.CIN(carry[k]));
 end endgenerate
 assign rounded={{30{word[33]}},word};
endmodule

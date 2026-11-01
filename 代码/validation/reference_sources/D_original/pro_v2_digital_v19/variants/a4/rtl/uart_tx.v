`timescale 1ns/1ps
// Module: uart_tx | Purpose: UART 8N1 transmitter, LSB first.
// Clock: CLK_HZ | Reset: low synchronous | Input: byte ready/valid.
// Output: idle-high serial pin | Latency: 10*round(CLK_HZ/BAUD) clocks/byte.
module uart_tx #(parameter CLK_HZ=50000000,BAUD=115200)
 (input clk,input rst_n,input valid,input [7:0] data,output ready,output tx);
    localparam DIV=(CLK_HZ+BAUD/2)/BAUD;
    reg [$clog2(DIV)-1:0] count;
    reg [3:0] bits_left;
    reg [9:0] shift;
    assign ready=bits_left==0;
    assign tx=ready ? 1'b1 : shift[0];
    always @(posedge clk) begin
        if(!rst_n) begin count<=0;bits_left<=0;shift<=10'h3ff;end
        else if(ready) begin
            if(valid) begin shift<={1'b1,data,1'b0};bits_left<=10;count<=DIV-1;end
        end else if(count==0) begin
            shift<={1'b1,shift[9:1]};bits_left<=bits_left-1'b1;count<=DIV-1;
        end else count<=count-1'b1;
    end
endmodule

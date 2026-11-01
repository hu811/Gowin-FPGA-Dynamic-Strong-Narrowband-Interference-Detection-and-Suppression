`timescale 1ns/1ps
// Module: uart_rx | Purpose: synchronized, center-sampled UART 8N1 command bytes.
// Clock: CLK_HZ | Reset: low synchronous | Input: asynchronous RX.
// Output: byte and one-clock valid; invalid stop bit discarded | Latency: ~9.5 bits.
module uart_rx #(parameter CLK_HZ=50000000,BAUD=115200)
 (input clk,input rst_n,input rx,output reg valid,output reg [7:0] data);
    localparam DIV=(CLK_HZ+BAUD/2)/BAUD;
    reg [1:0] sync;
    reg [1:0] state;
    reg [$clog2(DIV)-1:0] count;
    reg [2:0] bit_index;
    reg [7:0] shift;
    always @(posedge clk) begin
        if(!rst_n) begin sync<=3;state<=0;count<=0;bit_index<=0;shift<=0;data<=0;valid<=0;end
        else begin
            sync<={sync[0],rx}; valid<=0;
            case(state)
                0: if(!sync[1]) begin count<=DIV/2-1;state<=1;end
                1: if(count!=0) count<=count-1'b1;
                   else if(!sync[1]) begin count<=DIV-1;bit_index<=0;state<=2;end
                   else state<=0;
                2: if(count!=0) count<=count-1'b1;
                   else begin
                       shift[bit_index]<=sync[1];count<=DIV-1;
                       if(bit_index==7) state<=3;else bit_index<=bit_index+1'b1;
                   end
                3: if(count!=0) count<=count-1'b1;
                   else begin if(sync[1]) begin data<=shift;valid<=1;end state<=0;end
            endcase
        end
    end
endmodule

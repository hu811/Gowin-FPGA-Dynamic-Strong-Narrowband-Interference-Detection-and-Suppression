`timescale 1ns/1ps
// Module: reset_sync | Purpose: power-on reset and asynchronous button assertion.
// Clock: 50 MHz | Reset: active low, asynchronous assert, synchronous release.
// Input: board reset_n | Output: rst_n | Latency: 65536 clocks at power-on.
module reset_sync(input clk, input reset_n, output rst_n);
    reg [15:0] startup = 0;
    reg boot_done = 0;
    reg [1:0] sync = 0;
    always @(posedge clk) begin
        if (!boot_done) begin
            startup <= startup + 1'b1;
            if (&startup) boot_done <= 1'b1;
        end
    end
    always @(posedge clk or negedge reset_n) begin
        if (!reset_n) sync <= 0;
        else sync <= {sync[0], boot_done};
    end
    assign rst_n = sync[1];
endmodule

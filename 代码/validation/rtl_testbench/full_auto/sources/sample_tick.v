`timescale 1ns/1ps
// Module: sample_tick | Purpose: sample enable without a generated clock.
// Clock: CLK_HZ | Reset: low synchronous | Input: none | Output: 1-cycle tick.
// Latency: first tick after ceil(CLK_HZ/SAMPLE_HZ) clocks; <=1 clock jitter.
module sample_tick #(parameter CLK_HZ=50000000, SAMPLE_HZ=32000)
    (input clk, input rst_n, output reg tick);
    reg [31:0] accumulator;
    always @(posedge clk) begin
        if (!rst_n) begin accumulator<=0; tick<=0; end
        else if (accumulator >= CLK_HZ-SAMPLE_HZ) begin
            accumulator <= accumulator-(CLK_HZ-SAMPLE_HZ); tick<=1;
        end else begin accumulator<=accumulator+SAMPLE_HZ; tick<=0; end
    end
endmodule

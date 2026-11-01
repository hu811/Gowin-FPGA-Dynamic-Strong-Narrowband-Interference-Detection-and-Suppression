`timescale 1ns/1ps
// Purpose: one I2C write (address byte, control byte, payload), MSB first.
// Clock: CLK_HZ; reset: asynchronous low. request accepted only when !busy.
// Format: 7-bit address, two 8-bit bytes. done pulses once, error held to next request.
// Latency: about 0.32 ms at 100 kHz; bounded by 5 ms including clock stretching.
// SCL/SDA are open drain; external board pull-ups provide logic high.
module i2c_write3 #(
    parameter integer CLK_HZ=50000000,
    parameter integer I2C_HZ=100000
)(
    input clk, input rst_n, input request,
    input [6:0] address, input [7:0] control, input [7:0] payload,
    inout scl, inout sda,
    output reg busy, output reg done, output reg error
);
    localparam integer Q=CLK_HZ/(I2C_HZ*4);
    localparam integer LIMIT=(CLK_HZ/200)/Q;
    localparam IDLE=0, FREE=1, START=2, START_LOW=3, SET=4,
               RISE=5, HIGH=6, FALL=7, STOP_LOW=8,
               STOP_RISE=9, STOP_RELEASE=10, FINISH=11,
               START_HOLD=12, STOP_HOLD=13;
    reg [3:0] state;
    reg scl_low, sda_low;
    reg [1:0] scl_sync, sda_sync;
    reg [23:0] bits;
    reg [3:0] bit_count;
    reg [1:0] byte_count;
    reg [$clog2(Q)-1:0] quarter_count; reg [$clog2(LIMIT)-1:0] watchdog;
    assign scl=scl_low ? 1'b0 : 1'bz;
    assign sda=sda_low ? 1'b0 : 1'bz;
    always @(posedge clk or negedge rst_n) begin
        if(!rst_n) begin scl_sync<=3; sda_sync<=3; end
        else begin scl_sync<={scl_sync[0],scl}; sda_sync<={sda_sync[0],sda}; end
    end
    always @(posedge clk or negedge rst_n) begin
        if(!rst_n) begin
            state<=IDLE; scl_low<=0; sda_low<=0; busy<=0; done<=0; error<=0;
            bits<=0; bit_count<=0; byte_count<=0; quarter_count<=0; watchdog<=0;
        end else begin
            done<=0;
            if(!busy) begin
                quarter_count<=0; watchdog<=0;
                if(request) begin
                    bits<={address,1'b0,control,payload}; bit_count<=0; byte_count<=0;
                    busy<=1; error<=0; state<=FREE;
                end
            end else if(watchdog==LIMIT-1 && quarter_count==Q-1) begin
                // No slave or a stuck line must not hang the FPGA.
                scl_low<=0; sda_low<=0; busy<=0; done<=1; error<=1; state<=IDLE;
            end else begin
                if(quarter_count==Q-1) begin
                    watchdog<=watchdog+1'b1;
                    quarter_count<=0;
                    case(state)
                        FREE: if(scl_sync[1] && sda_sync[1]) state<=START;
                        START: begin sda_low<=1; state<=START_HOLD; end
                        START_HOLD: state<=START_LOW;
                        START_LOW: begin scl_low<=1; state<=SET; end
                        SET: begin
                            sda_low<=(bit_count==8) ? 1'b0 : !bits[23];
                            state<=RISE;
                        end
                        RISE: begin scl_low<=0; state<=HIGH; end
                        HIGH: if(scl_sync[1]) begin
                            if(bit_count==8 && sda_sync[1]) error<=1;
                            state<=FALL;
                        end
                        FALL: begin
                            scl_low<=1;
                            if(bit_count==8) begin
                                bit_count<=0;
                                if(error || byte_count==2) state<=STOP_LOW;
                                else begin byte_count<=byte_count+1'b1; state<=SET; end
                            end else begin
                                bits<={bits[22:0],1'b0}; bit_count<=bit_count+1'b1; state<=SET;
                            end
                        end
                        STOP_LOW: begin sda_low<=1; state<=STOP_RISE; end
                        STOP_RISE: begin scl_low<=0; state<=STOP_HOLD; end
                        STOP_HOLD: if(scl_sync[1]) state<=STOP_RELEASE;
                        STOP_RELEASE: if(scl_sync[1]) begin sda_low<=0; state<=FINISH; end
                        FINISH: begin busy<=0; done<=1; state<=IDLE; end
                        default: begin scl_low<=0; sda_low<=0; busy<=0; done<=1; error<=1; state<=IDLE; end
                    endcase
                end else quarter_count<=quarter_count+1'b1;
            end
        end
    end
endmodule

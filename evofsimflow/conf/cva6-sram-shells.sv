// CVA6Config, nangate45, synthesis of 2026-10 (DC 2025.06-SP4): Design Compiler
// leaves four cache SRAM stubs empty under a name no model has, suffix _2. Each
// shell runs on the model with the same parameters. flow.py adds this file
// after the models, and only when the netlist needs it.

module tc_sram_00000010_00000031_00000008_00000001_00000001_none_0_2 (
  input clk_i, rst_ni, input [0:0] req_i, we_i, input [3:0] addr_i,
  input [48:0] wdata_i, input [6:0] be_i, output [48:0] rdata_o);
  tc_sram_00000010_00000031_00000008_00000001_00000001_none_0 i_model (
    .clk_i(clk_i), .rst_ni(rst_ni), .req_i(req_i), .we_i(we_i), .addr_i(addr_i),
    .wdata_i(wdata_i), .be_i(be_i), .rdata_o(rdata_o));
endmodule

module tc_sram_00000010_00000200_00000008_00000001_00000001_none_0_2 (
  input clk_i, rst_ni, input [0:0] req_i, we_i, input [3:0] addr_i,
  input [511:0] wdata_i, input [63:0] be_i, output [511:0] rdata_o);
  tc_sram_00000010_00000200_00000008_00000001_00000001_none_0 i_model (
    .clk_i(clk_i), .rst_ni(rst_ni), .req_i(req_i), .we_i(we_i), .addr_i(addr_i),
    .wdata_i(wdata_i), .be_i(be_i), .rdata_o(rdata_o));
endmodule

module tc_sram_00000020_00000030_00000008_00000001_00000001_none_0_2 (
  input clk_i, rst_ni, input [0:0] req_i, we_i, input [4:0] addr_i,
  input [47:0] wdata_i, input [5:0] be_i, output [47:0] rdata_o);
  tc_sram_00000020_00000030_00000008_00000001_00000001_none_0 i_model (
    .clk_i(clk_i), .rst_ni(rst_ni), .req_i(req_i), .we_i(we_i), .addr_i(addr_i),
    .wdata_i(wdata_i), .be_i(be_i), .rdata_o(rdata_o));
endmodule

module tc_sram_00000020_00000080_00000008_00000001_00000001_none_0_2 (
  input clk_i, rst_ni, input [0:0] req_i, we_i, input [4:0] addr_i,
  input [127:0] wdata_i, input [15:0] be_i, output [127:0] rdata_o);
  tc_sram_00000020_00000080_00000008_00000001_00000001_none_0 i_model (
    .clk_i(clk_i), .rst_ni(rst_ni), .req_i(req_i), .we_i(we_i), .addr_i(addr_i),
    .wdata_i(wdata_i), .be_i(be_i), .rdata_o(rdata_o));
endmodule

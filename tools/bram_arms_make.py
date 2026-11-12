#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""results/pnr/bram_template_arms/make_arms.py -- throwaway TD arms separating two
candidate root causes for "delivered top does not pass the backend":
A (template): rtl/epv32_ram.v reads the array combinationally, allegedly outside
TD's BRAM-inference template.
B (capacity): main memory is 65536 x 32 bit = 2048 Kbit, but EG4S20BG256 ERAM = 1088 Kbit.

Every arm is a standalone .v, synthesized to EG4S20BG256 with area reports only.
Capacity = WORDS * 32 Kbit (8192 -> 256, 65536 -> 2048 = 1.88x device ERAM).

Writes ONLY inside results/pnr/bram_template_arms/. Console output is ASCII only (host console GBK).
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))

STEP_PROC = '''proc step {mark body} {
    if {[catch {uplevel 1 $body} err]} { puts "MARK-$mark-FAILED :: $err"; return 1 }
    puts "MARK-$mark-OK"
    return 0
}
'''

# Synthesis-only step table: same command shapes as flow/make_td_arms.py
# SYNTH_STEPS, plus report_ram_utilization (wrapped in step, so a failure there
# is recorded and does not eat the area reports).
STEPS = """step 010-import_device {import_device eagle_s20.db -package EG4S20BG256}
step 020-read_verilog  {read_verilog -file $FILES -top $TOP}
step 030-optimize_rtl  {optimize_rtl}
step 040-report_area_rtl {report_area -file rtl.area}
step 050-read_sdc      {read_sdc $SDC}
step 060-optimize_gate {optimize_gate}
step 061-report_area_gate {report_area -file gate.area}
step 062-report_qor_gate  {report_qor -step gate -file gate.qor}
step 063-report_ram_util  {report_ram_utilization -file ram_util.rpt}
"""

SDC = "create_clock -name clk -period 10.000 [get_ports {clk}]\n"


# ---------------------------------------------------------------- RTL bodies

def async_body(top, words):
    """Faithful copy of rtl/epv32_ram.v structure (async read + byte-enabled
    sync write + third backdoor write port)."""
    return '''// scratch arm %s -- copied structure from rtl/epv32_ram.v
//   asynchronous (combinational) read on BOTH ports
//   synchronous write with 4 byte enables
//   third (simulation backdoor) write port
// WORDS = %d  ->  declared capacity = %d Kbit
module %s (
  input  wire         clk,
  // data port
  input  wire         d_en,
  input  wire         d_we,
  input  wire [3:0]   d_be,
  input  wire [31:0]  d_addr,
  input  wire [31:0]  d_wdata,
  output wire [31:0]  d_rdata,
  // fetch port
  input  wire [31:0]  i_addr,
  output wire [31:0]  i_rdata,
  // backdoor write port
  input  wire         bld_en,
  input  wire [31:0]  bld_addr,
  input  wire [31:0]  bld_wdata
);
  localparam WORDS = %d;
  localparam AW = $clog2(WORDS);

  reg [31:0] mem [0:WORDS-1];

  wire [AW-1:0] didx = d_addr[AW+1:2];
  wire [AW-1:0] iidx = i_addr[AW+1:2];
  wire [AW-1:0] bidx = bld_addr[AW+1:2];

  assign d_rdata = mem[didx];
  assign i_rdata = mem[iidx];

  always @(posedge clk) begin
    if (bld_en) begin
      mem[bidx] <= bld_wdata;
    end else if (d_en && d_we) begin
      if (d_be[0]) mem[didx][7:0]   <= d_wdata[7:0];
      if (d_be[1]) mem[didx][15:8]  <= d_wdata[15:8];
      if (d_be[2]) mem[didx][23:16] <= d_wdata[23:16];
      if (d_be[3]) mem[didx][31:24] <= d_wdata[31:24];
    end
  end
endmodule
''' % (top, words, words * 32 // 1024, top, words)


def bram_body(top, words, byte_en):
    """BRAM-inferable template: registered read output on both ports, 2 ports
    only (no backdoor port). byte_en selects byte-write-enable vs full word."""
    write_src = """      if (d_be[0]) mem[didx][7:0]   <= d_wdata[7:0];
      if (d_be[1]) mem[didx][15:8]  <= d_wdata[15:8];
      if (d_be[2]) mem[didx][23:16] <= d_wdata[23:16];
      if (d_be[3]) mem[didx][31:24] <= d_wdata[31:24];""" if byte_en \
        else """      mem[didx] <= d_wdata;"""
    be_port = "  input  wire [3:0]   d_be,\n" if byte_en else ""
    return '''// scratch arm %s -- BRAM-inferable template
//   registered read output on BOTH ports
//   %s
//   2 ports only (no simulation backdoor port)
// WORDS = %d  ->  declared capacity = %d Kbit
module %s (
  input  wire         clk,
  // port B: read/write
  input  wire         d_en,
  input  wire         d_we,
%s  input  wire [31:0]  d_addr,
  input  wire [31:0]  d_wdata,
  output reg  [31:0]  d_rdata,
  // port A: read only (fetch)
  input  wire [31:0]  i_addr,
  output reg  [31:0]  i_rdata
);
  localparam WORDS = %d;
  localparam AW = $clog2(WORDS);

  reg [31:0] mem [0:WORDS-1];

  wire [AW-1:0] didx = d_addr[AW+1:2];
  wire [AW-1:0] iidx = i_addr[AW+1:2];

  always @(posedge clk) begin
    i_rdata <= mem[iidx];
  end

  always @(posedge clk) begin
    d_rdata <= mem[didx];
    if (d_en && d_we) begin
%s
    end
  end
endmodule
''' % (top,
       "synchronous write with 4 byte write enables" if byte_en
       else "synchronous full-word write enable (no byte enables)",
       words, words * 32 // 1024, top, be_port, words, write_src)


ARMS = {
    # name                              words   kind        byte_en
    "ram_async_256k":                   (65536, "async",    None),
    "ram_async_32k":                    (8192,  "async",    None),
    "ram_bram_32k":                     (8192,  "bram",     True),
    "ram_bram_256k":                    (65536, "bram",     True),
    "ram_bram_32k_nobe":                (8192,  "bram",     False),
    "ram_bram_256k_nobe":               (65536, "bram",     False),
    # Two more points on the async-read curve, small enough that TD reaches
    # report_area, so the distributed-RAM LUT cost of the delivered *shape* is
    # a measured number rather than an extrapolation.
    "ram_async_2k":                     (512,   "async",    None),
    "ram_async_8k":                     (2048,  "async",    None),
}


def write_tcl(path, top, vfile):
    body = ("# %s -- generated by results/pnr/bram_template_arms/make_arms.py\n"
            "# arm top = %s ; standalone RAM module, synthesis + area only\n"
            % (os.path.basename(path), top))
    body += 'set TOP %s\nset FILES {%s}\nset SDC ./top.sdc\n' % (top, vfile)
    body += 'puts "ARM-SCRATCH-%s"\n' % top
    body += STEP_PROC + STEPS
    body += "foreach f {rtl.area gate.area gate.qor ram_util.rpt} {\n"
    body += '    if {[file exists $f]} { puts "ARTIFACT-$f-SIZE=[file size $f]" }'
    body += ' else { puts "ARTIFACT-$f-MISSING" }\n}\n'
    body += 'puts "MARK-999-DONE-%s"\n' % top
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(body)


def main(argv):
    # Optional arm-name filter: never rewrite an arm whose job is still in
    # flight (TD has that .tcl / .v open).
    want = set(a for a in argv[1:] if not a.startswith("-"))
    for name, (words, kind, be) in sorted(ARMS.items()):
        if want and name not in want:
            continue
        d = os.path.join(ROOT, name)
        os.makedirs(d, exist_ok=True)
        v = os.path.join(d, name + ".v")
        if kind == "async":
            src = async_body(name, words)
        else:
            src = bram_body(name, words, be)
        with io.open(v, "w", encoding="utf-8", newline="\n") as f:
            f.write(src)
        with io.open(os.path.join(d, "top.sdc"), "w",
                     encoding="utf-8", newline="\n") as f:
            f.write(SDC)
        write_tcl(os.path.join(d, name + ".tcl"), name, "./%s.v" % name)
        print("ARM_MADE %-22s words=%-6d kbit=%-5d kind=%-6s byte_en=%s"
              % (name, words, words * 32 // 1024, kind, be))
    print("ARMS_TOTAL %d defined / %d written this run"
          % (len(ARMS), len(want) if want else len(ARMS)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

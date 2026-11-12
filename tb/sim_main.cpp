// ============================================================================
//  sim_main.cpp  --  Verilator C++ 测试台主程序
//
//  作用: 把编译好的 RV32IM(+EPV) 镜像后门装载进 SoC 主存, 复位释放后跑到
//        tohost 非零 / SYSC commit / 周期上限为止, 并把
//          * 逐条退休轨迹 (pc, rd, wdata) 与总线写轨迹
//          * UART 发送字节流
//          * 总周期数 / 退休指令数 / 最终通用寄存器组
//        落到文件, 供 Python 侧与黄金 ISS 逐条比对。
//
//  参数走环境变量而不是 plusarg: Verilator 的 plusarg 只识别 +verilator+ 前缀,
//  自己解析容易出静默的「参数没进去」。环境变量在本机最好验证。
// ============================================================================
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdint>
#include <string>
#include <vector>
#include <map>
#include <algorithm>
#include "Vepv32_soc.h"
#include "verilated.h"
#include "verilated_vcd_c.h"

double sc_time_stamp() { return 0.0; }

static std::string envs(const char* k, const char* dflt) {
  const char* v = std::getenv(k);
  return v ? std::string(v) : std::string(dflt);
}
static uint64_t envu(const char* k, uint64_t dflt) {
  const char* v = std::getenv(k);
  if (!v) return dflt;
  return strtoull(v, nullptr, 0);
}

// 镜像: objcopy -O binary 出来的平坦小端块, 从 EPV_BASE 起按字装载。
// (env/p/link.ld 把 text/tohost/data/bss 连续排在 0x8000_0000, 所以平坦装载
//  与 ELF 段布局一致; harness 会用 nm 复核 .text 段基址与 EPV_BASE 相同。)
struct Word { uint32_t idx, data; };

static std::vector<Word> load_img(const std::string& path, uint32_t base) {
  std::vector<Word> v;
  FILE* f = std::fopen(path.c_str(), "rb");
  if (!f) { std::fprintf(stderr, "IMAGE_OPEN_FAIL %s\n", path.c_str()); std::exit(2); }
  std::fseek(f, 0, SEEK_END);
  long n = std::ftell(f);
  std::fseek(f, 0, SEEK_SET);
  if (n <= 0) { std::fclose(f); return v; }
  std::vector<unsigned char> buf((n + 3) & ~3L, 0);
  if (std::fread(buf.data(), 1, (size_t)n, f) != (size_t)n) {
    std::fprintf(stderr, "IMAGE_READ_FAIL %s\n", path.c_str()); std::exit(2);
  }
  std::fclose(f);
  uint32_t w0 = base >> 2;
  for (size_t i = 0; i + 4 <= buf.size(); i += 4) {
    uint32_t d = (uint32_t)buf[i] | ((uint32_t)buf[i+1] << 8)
               | ((uint32_t)buf[i+2] << 16) | ((uint32_t)buf[i+3] << 24);
    v.push_back({w0 + (uint32_t)(i >> 2), d});
  }
  return v;
}

int main(int argc, char** argv) {
  Verilated::commandArgs(argc, argv);

  const std::string img        = envs("EPV_IMAGE", "");
  const uint64_t    img_base   = envu("EPV_BASE", 0x80000000);
  const uint64_t    maxcyc     = envu("EPV_MAXCYC", 2000000);
  const uint64_t    tohost     = envu("EPV_TOHOST", 0);
  const std::string trace_out  = envs("EPV_TRACE", "");
  const std::string result_out = envs("EPV_RESULT", "results/sim/last.json");
  const bool verbose           = envu("EPV_VERBOSE", 0) != 0;

  Vepv32_soc* top = new Vepv32_soc;

  // ---- 可选 VCD: 分派为什么没起来, 读代码推不动时直接看波形 ----
  VerilatedVcdC* tfp = nullptr;
  if (envu("EPV_WAVE", 0)) {
    Verilated::traceEverOn(true);
    tfp = new VerilatedVcdC;
    top->trace(tfp, 99);
    const std::string wf = envs("EPV_WAVE_FILE", "results/wave/epv.vcd");
    tfp->open(wf.c_str());
    std::fprintf(stderr, "[tb] wave -> %s\n", wf.c_str());
  }

  // ---------------- 复位 + 后门装载 ----------------
  top->clk = 0; top->rst_n = 0;
  top->bld_en = 0; top->bld_addr = 0; top->bld_wdata = 0;
  top->i_uart_rx = 1;

  std::vector<Word> words = load_img(img, (uint32_t)img_base);
  if (words.empty()) {
    std::fprintf(stderr, "EMPTY_IMAGE (EPV_IMAGE 未设置或文件为空)\n");
    std::exit(2);
  }
  // 装载期间保持复位, 每拍写一个字
  for (size_t i = 0; i < words.size(); ++i) {
    top->clk = 0; top->eval();
    top->bld_en     = 1;
    top->bld_addr   = (uint32_t)(words[i].idx << 2);
    top->bld_wdata  = words[i].data;
    top->clk = 1; top->eval();
  }
  top->bld_en = 0;
  if (verbose) std::fprintf(stderr, "[tb] loaded %zu words\n", words.size());

  // 复位释放后再给两拍, 让后门写的最后一个字稳定
  for (int i = 0; i < 2; ++i) { top->clk = 0; top->eval(); top->clk = 1; top->eval(); }
  top->rst_n = 1;

  FILE* tr = nullptr;
  if (!trace_out.empty()) {
    tr = std::fopen(trace_out.c_str(), "wb");
    if (!tr) { std::fprintf(stderr, "TRACE_OPEN_FAIL %s\n", trace_out.c_str()); std::exit(2); }
  }

  std::string uart;
  uint64_t cyc = 0, retired = 0, stalls = 0;
  uint32_t commit_val = 0; bool commit_seen = false;
  std::string stop = "MAXCYC";

  // 上一拍的总线写 (o_tr_* 与总线信号都是同拍输出, 记下来去重)
  while (cyc < maxcyc) {
    top->clk = 0; top->eval();
    if (tfp) tfp->dump((int)(2*cyc));
    top->clk = 1; top->eval();
    if (tfp) tfp->dump((int)(2*cyc)+1);
    ++cyc;

    if (top->o_tr_valid) {
      ++retired;
      if (tr) {
        // R <pc> <rd> <wdata>   /   S <addr> <data> <be>
        if (top->o_tr_regwr && top->o_tr_rd != 0)
          std::fprintf(tr, "R %08x %u %08x\n", top->o_tr_pc, (unsigned)top->o_tr_rd,
                       top->o_tr_wdata);
        if (top->o_tr_memwr)
          std::fprintf(tr, "S %08x %08x %u\n", top->o_tr_addr, top->o_tr_mdata,
                       (unsigned)top->o_tr_mbe);
      }
    } else ++stalls;

    // UART 发送捕获: 写 0x90000000 即视为输出一个字节
    if (top->o_tr_valid && top->o_tr_memwr && top->o_tr_addr == 0x90000000u) {
      char c = (char)(top->o_tr_mdata & 0xFF);
      uart.push_back(c);
    }

    if (top->o_commit) { commit_seen = true; commit_val = top->o_commit_val; }

    if (commit_seen) { stop = "COMMIT"; break; }

    if (tohost) {
      // tohost 在 RAM 里: 用总线读不划算, 改为监视对 tohost 地址的写
      if (top->o_tr_valid && top->o_tr_memwr &&
          (top->o_tr_addr & ~3u) == (tohost & ~3u)) { stop = "TOHOST"; break; }
    }
  }

  if (tr) std::fclose(tr);
  if (tfp) { tfp->flush(); tfp->close(); delete tfp; tfp = nullptr; }

  // ---------------- 结果落盘 ----------------
  {
    std::string dir = result_out;
    size_t p = dir.find_last_of("/\\");
    FILE* f = std::fopen(result_out.c_str(), "wb");
    if (!f) { std::fprintf(stderr, "RESULT_OPEN_FAIL %s\n", result_out.c_str()); std::exit(2); }
    std::fprintf(f, "{\n");
    std::fprintf(f, "  \"cycles\": %llu,\n", (unsigned long long)cyc);
    std::fprintf(f, "  \"retired\": %llu,\n", (unsigned long long)retired);
    std::fprintf(f, "  \"nonretire_cycles\": %llu,\n", (unsigned long long)stalls);
    std::fprintf(f, "  \"stop_reason\": \"%s\",\n", stop.c_str());
    std::fprintf(f, "  \"commit_seen\": %s,\n", commit_seen ? "true" : "false");
    std::fprintf(f, "  \"commit_val\": %llu,\n", (unsigned long long)commit_val);
    std::fprintf(f, "  \"tohost_addr\": %llu,\n", (unsigned long long)tohost);
    std::fprintf(f, "  \"image_words\": %zu,\n", words.size());
    std::fprintf(f, "  \"dbg_trap_cnt\": %u,\n", (unsigned)top->o_dbg_trap_cnt);
    std::fprintf(f, "  \"dbg_mepc\": \"0x%08x\",\n", (unsigned)top->o_dbg_mepc);
    std::fprintf(f, "  \"dbg_mtval\": \"0x%08x\",\n", (unsigned)top->o_dbg_mtval);
    std::fprintf(f, "  \"dbg_mcause\": \"0x%08x\",\n", (unsigned)top->o_dbg_mcause);
    std::fprintf(f, "  \"dbg_last_inst\": \"0x%08x\",\n", (unsigned)top->o_dbg_last_inst);
    std::string esc;
    for (char c : uart) {
      if (c == '"' || c == '\\') { esc.push_back('\\'); esc.push_back(c); }
      else if ((unsigned char)c < 0x20 || (unsigned char)c >= 0x7f) {
        char b[8]; std::snprintf(b, sizeof b, "\\u%04x", (unsigned)(unsigned char)c); esc += b;
      } else esc.push_back(c);
    }
    std::fprintf(f, "  \"uart\": \"%s\"\n}\n", esc.c_str());
    std::fclose(f);
  }

  if (verbose) {
    std::fprintf(stderr, "[tb] cyc=%llu retired=%llu stop=%s\n",
                 (unsigned long long)cyc, (unsigned long long)retired, stop.c_str());
    std::fprintf(stderr, "[tb] uart=%s\n", uart.c_str());
  }
#if VM_COVERAGE
  // 覆盖率计数只有在模型自己调用 write() 时才会落盘 —— 带 --coverage 重编而
  // 不写这一句, 结果是「构建成功、跑完了、盘上一个 .dat 都没有」, 而所有
  // 退出码仍然是 0。只有 --coverage 那一臂会走到这里 (认证臂 VM_COVERAGE=0,
  // 整块被预处理器摘掉, 所以认证二进制的内容不因本块而变)。
  {
    const std::string cov = envs("EPV_COV", "");
    if (!cov.empty()) {
      Verilated::mkdir("logs");
      Verilated::defaultContextp()->coveragep()->write(cov);
      std::fprintf(stderr, "[tb] COV_WRITE %s exists=%d\n", cov.c_str(),
                   (int)(std::fopen(cov.c_str(), "rb") != nullptr));
    }
  }
#endif

  delete top;
  return 0;
}

/*
 * Trusted replay driver for Stockfish SFNNv16. Linked with Stockfish under GPLv3.
 * This file, move generation, search, network loading and weights are immutable.
 * LOAD parses positions/moves before timing. RUN performs a fixed amount of work.
 * The operator times RUN outside this process; there are no per-call timers.
 */
#include <algorithm>
#include <cstdint>
#include <cstdlib>
#include <deque>
#include <iostream>
#include <memory>
#include <sstream>
#include <string>
#include <vector>

#include "attacks.h"
#include "evaluate.h"
#include "movegen.h"
#include "nnue/network.h"
#include "nnue/nnue_accumulator.h"
#include "position.h"
#include "uci.h"

using namespace Stockfish;
namespace NN = Stockfish::Eval::NNUE;

namespace {
[[noreturn]] void fail(const char* message) {
    std::cerr << message << '\n';
    std::exit(1);
}

struct Scene {
    std::unique_ptr<Position> pos = std::make_unique<Position>();
    std::deque<StateInfo> states{1};
    std::vector<Move> moves;
    std::vector<std::vector<Move>> siblings;
};

struct Totals {
    std::uint64_t calls = 0, checksum = 0;
    std::vector<Value> values;
};

void mix(Totals& total, Value value) {
    total.checksum ^= std::uint64_t(std::int64_t(value)) + 0x9e3779b97f4a7c15ULL
                    + (total.checksum << 6) + (total.checksum >> 2);
}

void move(Position& pos, Move m, StateInfo& state, NN::AccumulatorStack& stack) {
    const bool check = pos.gives_check(m);
    auto& dirty = stack.push();
    pos.do_move(m, state, check, dirty, nullptr, nullptr);
}

std::vector<std::unique_ptr<Scene>> load(std::size_t count, std::size_t branches,
                                       std::size_t salt) {
    if (count == 0 || count > 10000 || branches > 32)
        fail("Invalid corpus dimensions");
    std::vector<std::unique_ptr<Scene>> scenes;
    std::string line, fen, moves;
    for (std::size_t i = 0; i < count; ++i) {
        std::getline(std::cin, line);
        const bool chess960 = line == "1";
        std::getline(std::cin, fen);
        std::getline(std::cin, moves);
        auto scene = std::make_unique<Scene>();
        auto& pos = *scene->pos;
        if (pos.set(fen, chess960, &scene->states[0]).has_value())
            fail("Invalid FEN");
        std::istringstream tokens(moves);
        std::string token;
        while (true) {
            std::vector<Move> legal;
            for (Move m : MoveList<LEGAL>(pos))
                legal.push_back(m);
            if (!legal.empty())
                std::rotate(legal.begin(), legal.begin() + salt % legal.size(), legal.end());
            legal.resize(std::min(branches, legal.size()));
            scene->siblings.push_back(std::move(legal));
            if (!(tokens >> token))
                break;
            Move m = UCIEngine::to_move(pos, token);
            if (m == Move::none() || scene->moves.size() >= 192)
                fail("Illegal move or trace exceeds 192 plies");
            scene->moves.push_back(m);
            scene->states.emplace_back();
            pos.do_move(m, scene->states.back(), nullptr);
        }
        for (auto it = scene->moves.rbegin(); it != scene->moves.rend(); ++it)
            pos.undo_move(*it);
        scenes.push_back(std::move(scene));
    }
    return scenes;
}

Totals replay(const NN::Network& network, std::vector<std::unique_ptr<Scene>>& scenes,
              const std::string& mode, std::size_t passes, std::size_t offset) {
    if (passes == 0 || passes > 65536 || scenes.empty())
        fail("Invalid RUN dimensions");
    const bool lazy = mode == "check_lazy";
    const bool checking = mode == "check" || lazy;
    if (!checking && mode != "incremental" && mode != "refresh" && mode != "hot")
        fail("Invalid workload");
    auto stack = std::make_unique<NN::AccumulatorStack>();
    auto fresh = std::make_unique<NN::AccumulatorStack>();
    auto caches = std::make_unique<NN::AccumulatorCaches>(network);
    auto freshCaches = std::make_unique<NN::AccumulatorCaches>(network);
    Totals totals;
    auto eval = [&](Position& pos) {
        if (pos.checkers())
            return;  // Stockfish's static evaluation contract excludes check positions.
        const int repeats = mode == "hot" ? 8 : 1;
        for (int repeat = 0; repeat < repeats; ++repeat) {
            if (mode == "refresh")
                fresh->reset();
            auto& active = mode == "refresh" ? *fresh : *stack;
            const Value raw = network.evaluate(pos, active, *caches);
            ++totals.calls;
            mix(totals, raw);
            if (checking) {
                // Record every result, including undo/reuse and fresh comparisons.
                // The trusted evaluator compares this whole stream to the baseline.
                totals.values.push_back(raw);
                totals.values.push_back(Eval::evaluate(network, pos, *stack, *caches, 0));
                fresh->reset();
                freshCaches->clear(network);
                const Value freshRaw = network.evaluate(pos, *fresh, *freshCaches);
                const Value freshFinal = Eval::evaluate(network, pos, *fresh, *freshCaches, 0);
                totals.values.push_back(freshRaw);
                totals.values.push_back(freshFinal);
                if (raw != freshRaw || totals.values[totals.values.size() - 3] != freshFinal)
                    fail("Incremental and fresh outputs differ");
            }
        }
    };
    for (std::size_t pass = 0; pass < passes; ++pass) {
        for (std::size_t index = 0; index < scenes.size(); ++index) {
            auto& scene = *scenes[(index + offset + pass) % scenes.size()];
            auto& pos = *scene.pos;
            stack->reset();
            for (std::size_t ply = 0; ply <= scene.moves.size(); ++ply) {
                // Search can push several unevaluated positions before asking
                // NNUE to update. Test these lazy chains as well as eager ones.
                const bool evaluateHere = !lazy || ply % 4 == 0 || ply == scene.moves.size();
                if (evaluateHere)
                    eval(pos);
                if (checking && evaluateHere && !pos.checkers()) {
                    // Search reuses the same accumulator for a null move: only
                    // side to move changes. Exercise both perspectives and undo.
                    StateInfo nullState;
                    pos.do_null_move(nullState);
                    eval(pos);
                    pos.undo_null_move();
                    eval(pos);
                }
                for (Move sibling : scene.siblings[ply]) {
                    StateInfo branchState;
                    move(pos, sibling, branchState, *stack);
                    if (evaluateHere)
                        eval(pos);
                    pos.undo_move(sibling);
                    stack->pop();
                    if (checking && evaluateHere)
                        eval(pos);
                }
                if (ply < scene.moves.size())
                    move(pos, scene.moves[ply], scene.states[ply + 1], *stack);
            }
            for (auto it = scene.moves.rbegin(); it != scene.moves.rend(); ++it) {
                pos.undo_move(*it);
                stack->pop();
                if (checking)
                    eval(pos);
            }
        }
    }
    return totals;
}

void print(const Totals& totals) {
    std::cout << "{\"calls\":" << totals.calls << ",\"checksum\":\"" << totals.checksum
              << "\",\"values\":[";
    for (std::size_t i = 0; i < totals.values.size(); ++i) {
        if (i) std::cout << ',';
        std::cout << totals.values[i];
    }
    std::cout << "]}" << std::endl;
}
}  // namespace

int main(int argc, char** argv) {
    if (argc != 2)
        fail("Expected explicit network path");
    Attacks::init();
    Position::init();
    auto network = std::make_unique<NN::Network>();
    NN::EvalFile file;
    network->load_external({}, argv[1], file);
    if (!file.current.has_value())
        fail("Network failed to load");
    std::cout << "{\"ready\":true,\"network_bytes\":" << sizeof(NN::Network)
              << ",\"stack_bytes\":" << sizeof(NN::AccumulatorStack)
              << ",\"cache_bytes\":" << sizeof(NN::AccumulatorCaches) << "}" << std::endl;
    std::vector<std::unique_ptr<Scene>> scenes;
    std::string line;
    while (std::getline(std::cin, line)) {
        std::istringstream command(line);
        std::string verb;
        command >> verb;
        if (verb == "LOAD") {
            std::size_t count = 0, branches = 0, salt = 0;
            command >> count >> branches >> salt;
            scenes = load(count, branches, salt);
            std::cout << "{\"loaded\":" << scenes.size() << "}" << std::endl;
        } else if (verb == "RUN") {
            std::string mode;
            std::size_t passes = 0, offset = 0;
            command >> mode >> passes >> offset;
            print(replay(*network, scenes, mode, passes, offset));
        } else {
            fail("Unknown command");
        }
    }
}

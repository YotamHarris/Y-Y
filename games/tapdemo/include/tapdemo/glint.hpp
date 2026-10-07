#pragma once
#include <tapdemo/model.hpp>
#include <algorithm>
#include <cstdlib>
#include <utility>
#include <vector>

namespace tapdemo {
// What the player is told about the goal while it is hidden (a glint in the fog near it) and when a round is lost
// (the goal lifted out of the fog, and how many bricks away it was). Presentation only: nothing here writes the
// Model, so the field, the retry and the bot's measurements are what they were.

// Fogged cells within `glintReach` straight steps of the goal shimmer. The strength is flat for the goal's own cell
// and its four neighbours, so the glint says "over here", not "this brick", then falls off to nothing past the reach.
inline constexpr int glintReach=4;
// The debug panel's GLINT setting: how strong the shimmer is (0 off .. 3 high).
inline constexpr int glintLevels=4, defaultGlintLevel=2;
inline constexpr const char* glintNames[glintLevels]{"OFF","LOW","MEDIUM","HIGH"};
inline constexpr float glintScales[glintLevels]{0.0f,0.6f,1.0f,1.5f};
inline float glintScale(int level) { return glintScales[std::clamp(level,0,glintLevels-1)]; }
// Straight (diamond) steps from a cell to the goal; -1 with no goal.
inline int glintDistance(const Model& m, int column, int row) {
  if(m.goal<0) return -1;
  return std::abs(column-m.goal%m.columns)+std::abs(row-m.goal/m.columns);
}
// The shimmer's strength `distance` steps from the goal: `glintScale(level)` out to one step, then falling in even
// steps to a quarter of it at the reach, and 0 beyond it (and for a negative distance).
inline float glintStrength(int distance, int level=defaultGlintLevel) {
  if(distance<0 || distance>glintReach) return 0;
  return glintScale(level)*static_cast<float>(glintReach+1-std::max(distance,1))/glintReach;
}

// The loss screen's reveal. How far the goal was from the nearest open cell when the last ball ended, in straight
// steps: the measure docs/levels.md uses for how near a loss was.
inline int nearMissBricks(const Model& m) {
  return m.goal<0 ? 0 : m.fogDistance(m.goal%m.columns,m.goal/m.columns);
}
// The shortest straight run from the goal to an open cell, goal first and the open cell last, so one more cell than
// nearMissBricks. Each step moves to a neighbour one step nearer an open cell (up, down, left, right in that order).
inline std::vector<int> nearMissPath(const Model& m) {
  std::vector<int> path;
  if(m.goal<0) return path;
  int column=m.goal%m.columns, row=m.goal/m.columns;
  path.push_back(m.goal);
  while(m.fogDistance(column,row)>0) {
    const int want=m.fogDistance(column,row)-1;
    bool moved=false;
    for(const auto [dc,dr]: {std::pair{0,-1},{0,1},{-1,0},{1,0}}) {
      const int c=column+dc, r=row+dr;
      if(c<0 || r<0 || c>=m.columns || r>=m.rows || m.fogDistance(c,r)!=want) continue;
      column=c; row=r; moved=true; path.push_back(r*m.columns+c); break;
    }
    if(!moved) break;
  }
  return path;
}
// Cells the loss screen lifts the fog from: the straight run to the open cell and the goal's surroundings.
inline constexpr int missRadius=2;
inline std::vector<char> nearMissLift(const Model& m) {
  std::vector<char> lift(m.columns*m.rows,0);
  if(m.goal<0) return lift;
  for(int row=0; row<m.rows; ++row) for(int column=0; column<m.columns; ++column)
    if(glintDistance(m,column,row)<=missRadius) lift[row*m.columns+column]=1;
  for(int cell: nearMissPath(m)) lift[cell]=1;
  return lift;
}
// The reveal takes this long before the retry is offered; a tap skips what is left of it.
inline constexpr float missSeconds=1.2f;
// How far through its reveal a cell `distance` steps from the goal is at `time` seconds: 0 still fogged, 1 clear. The
// goal clears first; far cells wait a little, never past the first four steps, so the whole reveal ends before missSeconds.
inline float missProgress(float time, int distance) {
  return std::clamp((time-0.2f-0.1f*std::min(distance,4))/0.5f,0.0f,1.0f);
}
}

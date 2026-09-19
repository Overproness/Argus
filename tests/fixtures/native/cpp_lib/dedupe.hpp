#pragma once
#include <algorithm>
#include <vector>

// Keeps the first occurrence of each value, searching the output each time: O(n^2) on unique input.
inline std::vector<int> dedupe(const std::vector<int> &xs) {
    std::vector<int> out;
    for (int x : xs)
        if (std::find(out.begin(), out.end(), x) == out.end())
            out.push_back(x);
    return out;
}

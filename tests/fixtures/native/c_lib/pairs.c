#include "pairs.h"

/* Counts pairs of equal values by comparing every pair: O(n^2). */
long count_equal_pairs(const int *xs, int n) {
    long count = 0;
    for (int i = 0; i < n; i++)
        for (int j = i + 1; j < n; j++)
            if (xs[i] == xs[j])
                count++;
    return count;
}

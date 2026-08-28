// Added by the change: the sample project gains a C++ file.
#include "util.h"

namespace sample {

int count_over(const int *values, int size, int limit) {
    int found = 0;
    for (int i = 0; i < size; ++i) {
        if (clamp(values[i], 0, limit) == limit) {
            found += 1;
        }
    }
    return found;
}

}  // namespace sample

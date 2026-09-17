#include <chrono>
#include <future>
#include <thread>
#include <vector>

namespace eng {
class Engine {
public:
    int step(std::future<int>& fut) {
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
        return fut.get();
    }

    void run(std::vector<std::future<int>>& fs) {
        for (auto& f : fs) {
            for (auto& g : fs) {
                step(g);
            }
        }
    }
};
}  // namespace eng

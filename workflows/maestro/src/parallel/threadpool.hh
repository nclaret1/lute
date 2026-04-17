#ifndef LWM_PARALLEL_THREADPOOL_HH
#define LWM_PARALLEL_THREADPOOL_HH

#include "spdlog/sinks/stdout_color_sinks.h"

#include <atomic>
#include <condition_variable>
#include <deque>
#include <exception>
#include <functional>
#include <future>
#include <memory>
#include <mutex>
#include <thread>
#include <type_traits>
#include <vector>

namespace LWM {
  class ThreadPool {
  public:
    explicit ThreadPool(size_t num_threads);
    ~ThreadPool();

    template <class Fn, class... Args>
    auto enqueue(Fn&& job, Args&&... args) {
      using return_t = std::invoke_result_t<Fn, Args...>;
      std::shared_ptr<std::promise<return_t>> promise =
          std::make_shared<std::promise<return_t>>();
      auto wrapped_future = promise->get_future();
      auto wrapped_job = [promise,
                          job = std::forward<Fn>(job),
                          ... args = std::forward<Args>(args)]() mutable {
        try {
          if constexpr (std::is_void_v<return_t>) {
            // Promise cannot be set with void value
            std::invoke(std::move(job), std::move(args)...);
            promise->set_value();
          } else {
            promise->set_value(std::invoke(std::move(job), std::move(args)...));
          }
        } catch (...) {
          // Catch the exceptions in the future
          promise->set_exception(std::current_exception());
        }
      };
      m_logger->debug("Attempting to queue a job.");
      enqueue_impl(std::move(wrapped_job));
      return wrapped_future;
    }

  private:
    std::atomic_bool m_running {true};
    std::mutex m_mutex;
    std::condition_variable m_job_or_term_cv;

    size_t m_num_threads;
    std::vector<std::thread> m_worker_pool;
    std::deque<std::function<void()>> m_jobs;

    void enqueue_impl(std::function<void()> job);

    void run_jobs() noexcept;

    std::shared_ptr<spdlog::logger> m_logger;
  };
} // namespace LWM

#endif // LWM_PARALLEL_THREADPOOL_HH


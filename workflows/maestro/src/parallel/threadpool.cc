#include "threadpool.hh"

#include "spdlog/spdlog.h"
#include "spdlog/sinks/stdout_color_sinks.h"

#include <functional>
#include <mutex>
#include <string>
#include <thread>

namespace LWM {
  ThreadPool::ThreadPool(size_t num_threads)
    : m_num_threads(num_threads)
    , m_logger([] {
      if (auto tmp = spdlog::get("LWM:ThreadPool")) {
        return tmp;
      } else {
        return spdlog::stdout_color_mt("LWM:ThreadPool");
      }
    }())
  {
    for (size_t i=0; i < m_num_threads; ++i) {
      m_worker_pool.emplace_back(&ThreadPool::run_jobs, this);
    }
    std::string msg{"created thread pool with " + std::to_string(m_num_threads)};
    msg += " threads";
    m_logger->debug(msg);
  }

  ThreadPool::~ThreadPool() {
    m_running = false;
    m_job_or_term_cv.notify_all();
    m_logger->debug("Waiting on threads to exit.");
    for (auto& worker_thread : m_worker_pool) {
      worker_thread.join();
    }
    m_logger->debug("Exiting.");
  }

  void ThreadPool::enqueue_impl(std::function<void()> job) {
    std::unique_lock<std::mutex> lock(m_mutex);
    m_jobs.emplace_back(std::move(job));
    m_job_or_term_cv.notify_one();
  }

  void ThreadPool::run_jobs() noexcept {
    while (m_running) {
      // Will keep job thread local
      thread_local std::function<void()> job;
      {
        std::unique_lock<std::mutex> lock(m_mutex);
        m_job_or_term_cv.wait(lock, [&] { return !m_jobs.empty() || !m_running; });
        if (!m_running) {
          break;
        }
        job.swap(m_jobs.front());
        m_jobs.pop_front();
      } // Release lock
      m_logger->debug("Attempting to run a queued job.");
      // Run job
      job();
    }
  }

} // namespace LWM


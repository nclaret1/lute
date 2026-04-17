#ifndef HTTP_SERVER_HH
#define HTTP_SERVER_HH

#include "handler.hh"
#include "http.hh"

#include "spdlog/sinks/stdout_color_sinks.h"

#include <sys/epoll.h>
#include <sys/socket.h>
#include <sys/types.h>

#include <array>
#include <atomic>
#include <chrono>
#include <csignal>
#include <functional>
#include <map>
#include <memory>
#include <mutex>
#include <random>
#include <string>
#include <thread>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

namespace HTTP {
  constexpr size_t read_size = 4096;

  class Server {
  public:
    explicit Server(const std::string& host, std::uint16_t port);
    Server(Server&& other)
      : m_logger(spdlog::stdout_color_mt("HTTP:Server"))
      , m_throttled_logger(m_logger, std::chrono::milliseconds(100))
    {
      m_host = std::move(other.m_host);
      m_port = std::move(other.m_port);
      m_sock_fd = std::move(other.m_sock_fd);
    }

    /**
     * Provide specific handlers for a route and HTTP method.
     * If no handler is provided and a client attempts to request the path and
     * method, then a 405 method not allowed is returned.
     *
     * @param[in] route The route/path that should be the functor handles
     * @param[in] method The method to use handler for (e.g. GET). See #HTTP::METHOD
     * @param[in] handler The functor that handles the request. See #HTTP::Handler
     */
    void add_request_handler(const std::string& route,
                             const METHOD method,
                             std::shared_ptr<Handler> handler) {
      m_request_handlers[route][method] = handler;
    }

    /**
     * Whether the server is currently running.
     *
     * @return m_running Whether the server is running.
     */
    bool running() { return m_running; }

    /**
     * The IP the server has/will bind. E.g. 0.0.0.0 for all interfaces.
     */
    std::string host() { return m_host; }

    /**
     * The port the server has/will bind.
     */
    std::uint16_t port() { return m_port; }

    /**
     * Start the server to begin receiving requests.
     * This function cannot be called twice without first calling `stop` in
     * between.
     */
    void start();

    /**
     * Stop the server. This shuts down all handling threads.
     */
    void stop();

  private:
    std::string m_host;
    std::uint16_t m_port;

    std::map<std::string, std::map<METHOD, std::shared_ptr<Handler>>> m_request_handlers;

    std::atomic_bool m_running{false};

    static constexpr unsigned m_max_events = 10000;
    static constexpr unsigned m_num_threads = 5;
    static constexpr unsigned m_backlog_size = 1000;

    std::thread m_event_threads[m_num_threads]; ///< Threads for processing requests
    int m_sock_fd; ///< Bound socket fd the server is listening on.
    int m_epoll_fd; ///< epoll file descriptor. Shared by all threads.

    // Shared buffers protected behind shard and its mutexes
    static constexpr size_t m_shard_count = 64;
    class Shard {
    public:
      std::mutex shard_mutex;
      std::unordered_set<int> fds;
      std::unordered_set<int> non_persistent_fds;
      std::unordered_map<int, std::vector<char>> buffers;
      std::unordered_map<int, int> missing_chunk;
      std::unordered_map<int, std::mutex> fd_mutexes;
      std::unordered_map<int, std::mutex> chunk_mutexes;
    };
    std::array<Shard, m_shard_count> m_shards;
    size_t shard_index(int fd) const noexcept {
      return std::hash<int>{}(fd) % m_shard_count;
    }

    epoll_event m_worker_events[m_num_threads][m_max_events];

    void process_events(int thread_idx);
    void handle_event(epoll_event& event, int thread_idx);

    void del_epoll_event(epoll_event& event);
    void mod_epoll_event(epoll_event& event);
    void mod_epoll_event(epoll_event& event, int TYPE);

    void handle_raw_http(epoll_event event);
    Response handle_request(const Request& request);

    std::string response_to_string(const Response& response);

    std::shared_ptr<spdlog::logger> m_logger;
    class LogThrottler {
    public:
      LogThrottler(std::shared_ptr<spdlog::logger> logger, std::chrono::milliseconds update_interval)
        : m_logger(std::move(logger))
        , m_update_interval(update_interval)
        , m_last_log(std::chrono::steady_clock::now())
      {}

      LogThrottler(const LogThrottler&) = default;
      LogThrottler(LogThrottler&&) noexcept = default;
      LogThrottler& operator=(const LogThrottler&) = default;
      LogThrottler& operator=(LogThrottler&&) noexcept = default;

      void info(const std::string& msg) {
        auto now = std::chrono::steady_clock::now();
        if (now - m_last_log >= m_update_interval) {
          m_logger->info(msg);
          m_last_log = now;
        }
      }

      void debug(const std::string& msg) {
        auto now = std::chrono::steady_clock::now();
        if (now - m_last_log >= m_update_interval) {
          m_logger->debug(msg);
          m_last_log = now;
        }
      }

    private:
      std::shared_ptr<spdlog::logger> m_logger;
      std::chrono::milliseconds m_update_interval;
      std::chrono::steady_clock::time_point m_last_log;
    };

    LogThrottler m_throttled_logger;
  };
} // Namespace HTTP

#endif

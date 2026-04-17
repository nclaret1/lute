#include "server.hh"

#include "http.hh"

#include "spdlog/spdlog.h"
#include "spdlog/sinks/stdout_color_sinks.h"

#include <arpa/inet.h>
#include <asm-generic/socket.h>
#include <fcntl.h>
#include <memory>
#include <mutex>
#include <netinet/in.h>
#include <ostream>
#include <sys/epoll.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <thread>
#include <unistd.h>

#include <atomic>
#include <cerrno>
#include <chrono>
#include <csignal>
#include <cstring>
#include <functional>
#include <iostream>
#include <map>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>

namespace {
  inline std::string thread_id_str() {
    std::hash<std::thread::id> hasher;
    return std::to_string(hasher(std::this_thread::get_id()));
  }
} // anonymous namespace

namespace HTTP {
  Server::Server(const std::string& host, std::uint16_t port)
    : m_host(host)
    , m_port(port)
    , m_logger([] {
      if (auto tmp = spdlog::get("HTTP:Server")) {
        return tmp;
      } else {
        return spdlog::stdout_color_mt("HTTP:Server");
      }
    }())
    , m_throttled_logger(m_logger, std::chrono::milliseconds(100))
  {
    if ((m_sock_fd = socket(AF_INET, SOCK_STREAM | SOCK_NONBLOCK, 0)) < 0) {
      throw std::runtime_error("Unable to create TCP socket.");
    }
  }

  void Server::start() {
    int opt{1};
    sockaddr_in server_addr;

    std::string msg {
      "Starting server on " + m_host + ":" + std::to_string(m_port) + " with " +
      std::to_string(m_num_threads) + " threads, using " +
      std::to_string(m_shard_count) + " shards, with a backlog size of " +
      std::to_string(m_backlog_size) + " and " +
      std::to_string(m_max_events) + " maximum events."
    };
    m_logger->info(msg);
    if (setsockopt(m_sock_fd, SOL_SOCKET, SO_REUSEADDR | SO_REUSEPORT, &opt,
                   sizeof(int)) < 0) {
      m_logger->critical("Unable to set socket settings SO_REUSEADDR | SO_REUSEPORT");
      throw std::runtime_error("Failed to set socket options.");
    }

    server_addr.sin_family = AF_INET;
    server_addr.sin_addr.s_addr = INADDR_ANY; // All interfaces
    inet_pton(AF_INET, m_host.c_str(), &(server_addr.sin_addr.s_addr));
    server_addr.sin_port = htons(m_port);

    if (bind(m_sock_fd, reinterpret_cast<sockaddr*>(&server_addr),
             sizeof(sockaddr_in)) < 0) {
      m_logger->critical("Unable to bind socket for " + m_host + ":" + std::to_string(m_port));
      throw std::runtime_error("Failed to bind socket.");
    }

    int flags = fcntl(m_sock_fd, F_GETFL, 0);
    if (flags == -1) {
      m_logger->critical("fcntl (F_GETFL) failed.");
      throw std::runtime_error("Error with fcntl F_GETFL.");
    }
    if (fcntl(m_sock_fd, F_SETFL, flags | O_NONBLOCK) < 0) {
      m_logger->critical("fnctl (F_SETFL) failed (using " + std::to_string(flags) + "| O_NONBLOCK)");
      throw std::runtime_error("Error with fcntl F_SETFL.");
    }
    if (::listen(m_sock_fd, m_backlog_size) < 0) {
      m_logger->critical("Unable to listen on socket. Backlog size of: " + std::to_string(m_backlog_size));
      throw std::runtime_error("Failed to listen on the port.");
    }

    m_running = true;

    if ((m_epoll_fd = epoll_create1(EPOLL_CLOEXEC)) < 0) {
      m_logger->critical("Unable to create epoll. epoll_create1.");
      throw std::runtime_error("Unable to create epoll FD.");
    }
    epoll_event event;
    memset(&event, 0, sizeof(event));
    event.data.fd = m_sock_fd;
    event.events = EPOLLIN;
    if (epoll_ctl(m_epoll_fd, EPOLL_CTL_ADD, m_sock_fd, &event) < 0) {
      m_logger->critical("Unable to add socket to epoll.");
      throw std::runtime_error("Unable to add socket with epoll_ctl.");
    }

    for (size_t i = 0; i < m_num_threads; i++) {
      m_event_threads[i] = std::thread(&Server::process_events, this, i);
    }
  }

  void Server::stop() {
    m_running = false;
    m_logger->info("Shutting down server...");
    for (size_t i_worker=0; i_worker < m_num_threads; ++i_worker) {
      m_event_threads[i_worker].join();
    }

    close(m_sock_fd);
    m_logger->info("All server threads exited.");
  }

  void Server::process_events(int thread_idx) {
    bool active{true};

    while (m_running) {
      if (!active) {
        std::this_thread::sleep_for(std::chrono::microseconds(100));
        active = true;
      }
      int nfds = epoll_wait(m_epoll_fd, m_worker_events[thread_idx], m_max_events, 0);
      if (nfds <= 0) {
        active = false;
        continue;
      }
      for (ssize_t i = 0; i < nfds; ++i) {
        epoll_event& current_event = m_worker_events[thread_idx][i];
        if ((current_event.events & EPOLLHUP) ||
            (current_event.events & EPOLLERR)) {
          del_epoll_event(current_event);
          continue;
        } else if (current_event.data.fd == m_sock_fd) {
          for (;;) {
            sockaddr_in client_addr;
            socklen_t client_len = sizeof(client_addr);

            int client_fd = accept4(m_sock_fd, reinterpret_cast<sockaddr*>(&client_addr),
                                    &client_len, SOCK_NONBLOCK);
            if (client_fd < 0) {
              if (errno == EAGAIN || errno == EWOULDBLOCK) {
                // No clients to accept right now
                break;
              } else if (errno == EINTR) {
                // Interrupted, try again
                continue;
              } else {
                m_logger->error("Failure in accept4, err: " + std::to_string(client_fd));
                break;
              }
            } else {
              int flags = fcntl(client_fd, F_GETFL, 0);
              if (flags == -1) {
                m_logger->error("Error: fcntl(F_GETFL)");
                close(client_fd);
                continue;
              }
              if (fcntl(client_fd, F_SETFL, flags | O_NONBLOCK) < 0) {
                m_logger->error("Error: fcntl(F_SETFL)");
                close(client_fd);
                continue;
              }
              epoll_event event;
              memset(&event, 0, sizeof(event));
              event.data.fd = client_fd;
              event.events = EPOLLIN | EPOLLET;
              if (epoll_ctl(m_epoll_fd, EPOLL_CTL_ADD, client_fd, &event) < 0) {
                m_logger->error("Error: epoll_ctl(EPOLL_CTL_ADD)");
                continue;
              }
              {
                size_t shard_id = shard_index(client_fd);
                Shard& shard = m_shards[shard_id];
                std::mutex& fd_mutex = shard.fd_mutexes.try_emplace(client_fd).first->second;
                std::scoped_lock lock(shard.shard_mutex, fd_mutex);
                auto& fds = shard.fds;
                fds.insert(client_fd);
                shard.buffers.try_emplace(client_fd);
              }
            }
          }
        } else if ((current_event.events & EPOLLIN) ||
                   (current_event.events & EPOLLOUT)) {
          handle_event(current_event, thread_idx);
        } else {
          del_epoll_event(current_event);
        }
      }
    }
  }

  void Server::handle_event(epoll_event& event, int thread_idx) {
    int fd = event.data.fd;
    size_t shard_id = shard_index(fd);
    Shard& shard = m_shards[shard_id];
    std::mutex* fd_mutex_ptr;
    {
      std::lock_guard<std::mutex> shard_lock(shard.shard_mutex);
      fd_mutex_ptr = &shard.fd_mutexes.try_emplace(fd).first->second;
      shard.chunk_mutexes.try_emplace(fd);
      shard.buffers.try_emplace(fd);
    }
    std::lock_guard<std::mutex> buf_lock(*fd_mutex_ptr);
    auto& buffer = shard.buffers[fd];
    if (event.events & EPOLLIN) {
      size_t offset{0};
      int local_read_size{read_size};
      bool reading_missing{false};
      {
        std::lock_guard<std::mutex> shard_lock(shard.shard_mutex);
        if (shard.missing_chunk.find(event.data.fd) != shard.missing_chunk.end()
            && shard.missing_chunk[event.data.fd]) {
          local_read_size = shard.missing_chunk[event.data.fd];
          reading_missing = true;
          offset = buffer.size();
        }
      }
      if (offset) {
        buffer.resize(offset + local_read_size);
      } else if (buffer.size() == 0){
        buffer.resize(local_read_size);
      }
      ssize_t n_bytes = recv(event.data.fd, buffer.data() + offset, local_read_size, 0);
      if (reading_missing && n_bytes == shard.missing_chunk[event.data.fd]) {
        {
          std::scoped_lock lock(shard.shard_mutex, shard.chunk_mutexes[fd]);
          shard.missing_chunk.erase(event.data.fd);
        }
        std::string msg{
          "Read missing: " + std::to_string(n_bytes) + " "
          "(fd: " + std::to_string(event.data.fd) + ") " +
          "[Thread: " + thread_id_str() + "]"
        };
        m_logger->trace(msg);
      }
      if (n_bytes < 0) {
        if (errno == EAGAIN || errno == EWOULDBLOCK) {
          // Try again later
          mod_epoll_event(event, EPOLLIN);
        } else {
          // Some other error
          // CANNOT USE `buffer` after this point!
          std::lock_guard<std::mutex> shard_lock(shard.shard_mutex);
          shard.buffers.erase(event.data.fd);
          del_epoll_event(event);
        }
      } else if (!n_bytes) {
        // No data/connection closed.
        // CANNOT USE `buffer` after this point!
        std::lock_guard<std::mutex> shard_lock(shard.shard_mutex);
        shard.buffers.erase(event.data.fd);
        del_epoll_event(event);
      } else {
        // Truncate back down as needed
        buffer.resize(offset + n_bytes);
        std::string msg{"Resized buffer " + std::to_string(event.data.fd) +
                        " to: " + std::to_string(offset + n_bytes) +
                        " [Thread: " + thread_id_str() + "]"};
        m_logger->trace(msg);
        // handle_raw_http rearms the FD
        handle_raw_http(event);
      }
    } else if (event.events & EPOLLOUT ){ // EPOLLOUT - We're writing
      // handle_raw_http fills a set of buffers we can reuse
      ssize_t n_bytes = send(event.data.fd, buffer.data(), buffer.size(), 0);
      if (n_bytes < 0) {
        if (errno == EAGAIN || errno == EWOULDBLOCK) {
          // Can try again later
          mod_epoll_event(event, EPOLLOUT);
        } else {
          // Error
          // CANNOT USE `buffer` after this point!
          std::lock_guard<std::mutex> shard_lock(shard.shard_mutex);
          shard.buffers.erase(event.data.fd);
          del_epoll_event(event);
        }
      } else {
        if (static_cast<size_t>(n_bytes) == buffer.size()) {
          if (shard.non_persistent_fds.find(fd) != shard.non_persistent_fds.end()) {
            // This is the non-persistent, connection close case
            std::lock_guard<std::mutex> shard_lock(shard.shard_mutex);
            // Check again to make sure its there?
            shard.non_persistent_fds.erase(fd);
            shard.buffers.erase(event.data.fd);
            del_epoll_event(event);
          } else {
            // This is the case for persistent connections
            buffer.clear();
            // All data sent. Ready for new data.
            mod_epoll_event(event, EPOLLIN);
          }
        } else {
          // Need to finish writing
          // Resize the buffer
          buffer.erase(buffer.begin(), buffer.begin() + n_bytes);
          mod_epoll_event(event, EPOLLOUT);
        }
      }
    }
  }

  /**
   * Remove a file descriptor (thats attached to event) from monitoring.
   * This function MUST be called with a lock on the shard_mutex.
   * It also removes the fd from the out of band monitoring done by the user
   * code which is not a thread safe operation.
   *
   * @param event The event containing the file descriptor to remove from
   &        monitoring.
   */
  void Server::del_epoll_event(epoll_event& event) {
    size_t shard_id = shard_index(event.data.fd);
    Shard& shard = m_shards[shard_id];
    auto& fds = shard.fds;

    if (fds.find(event.data.fd) == fds.end()) {
      // Already did this
      return;
    }
    if (epoll_ctl(m_epoll_fd, EPOLL_CTL_DEL, event.data.fd, nullptr) < 0) {
      if (errno != EBADF && errno != ENOENT) {
        m_logger->error("epoll_ctl(EPOLL_CTL_DEL) failed to remove fd.");
      }
    }
    close(event.data.fd);
    fds.erase(event.data.fd);
  }

  void Server::mod_epoll_event(epoll_event& event, int TYPE=EPOLLOUT) {
    event.events = TYPE | EPOLLET | EPOLLONESHOT;
    epoll_ctl(m_epoll_fd, EPOLL_CTL_MOD, event.data.fd, &event);
  }

  void Server::handle_raw_http(epoll_event event) {
    size_t shard_id = shard_index(event.data.fd);
    Shard& shard = m_shards[shard_id];
    auto& buffer = shard.buffers[event.data.fd];
    if (buffer.empty()) {
      // This should NOT happen - if it does something is incorrect in the locking
      // and management of shared resources. But we can recover, so no abort is done.
      m_logger->error("Empty request made it into HTTP handling routine!");
      mod_epoll_event(event, EPOLLIN);
      return;
    }
    // Must use begin and end to avoid truncating null-bytes
    std::string request_string(buffer.begin(), buffer.end());

    Request request;
    Response response;

    try {
      request = Request(request_string);
      auto headers = request.headers();
      if (headers.count("Content-Length")) {
        int diff = std::stoi(headers["Content-Length"])
                   - static_cast<int>(request.content_length());
        if (diff) {
          // Need to read more data
          {
            std::lock_guard<std::mutex> shard_lock(shard.shard_mutex);
            shard.missing_chunk[event.data.fd] = diff;
          }
          // Don't think lock needs to be held at this point?
          mod_epoll_event(event, EPOLLIN);
          return;
        }
      }
      response = handle_request(request);
      if (!request.persistent()) {
        response.set_header("Connection", "close");
        {
          std::lock_guard<std::mutex> shard_lock(shard.shard_mutex);
          shard.non_persistent_fds.insert(event.data.fd);
        }
      }
    } catch (const IncompleteHeader& e) {
      // Need more data for headers
      mod_epoll_event(event, EPOLLIN);
      return;
    } catch (const std::invalid_argument& e) {
      response = Response(CODE::BadRequest);
      response.set_content(e.what());
      m_logger->debug(std::string("Error in handle_raw_http: ") + e.what());
    } catch (const std::logic_error& e) {
      response = Response(CODE::BadRequest);
      response.set_content(e.what());
      m_logger->debug(std::string("Error in handle_raw_http: ") + e.what());
    } catch (const std::exception& e) {
      response = Response(CODE::BadRequest);
      response.set_content(e.what());
      m_logger->debug(std::string("Error in handle_raw_http: ") + e.what());
    }

    // Set response to write to client
    std::string response_string = response.to_string();
    std::fill(buffer.begin(), buffer.end(), 0);
    buffer.resize(response_string.size());
    std::copy(response_string.begin(), response_string.end(), buffer.begin());
    // Rearm the fd
    mod_epoll_event(event, EPOLLOUT);
  }

  Response Server::handle_request(const Request& request) {
    auto route_it = m_request_handlers.find(request.url());
    if (route_it != m_request_handlers.end()) {
      auto callback_it = route_it->second.find(request.method());
      if (callback_it != route_it->second.end()) {
        return (*(callback_it->second))(request);
      } else {
        return Response(CODE::MethodNotAllowed);
      }
    } else {
      return Response(CODE::NotFound);
    }
  }

} // namespace HTTP

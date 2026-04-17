#ifndef LWM_LAUNCHER_HH
#define LWM_LAUNCHER_HH

#include "../server/handler.hh"
#include "../server/http.hh"
#include "job.hh"

#include "spdlog/spdlog.h"
#include "spdlog/sinks/stdout_color_sinks.h"

#include <atomic>
#include <future>
#include <map>
#include <memory>
#include <mutex>
#include <optional>
#include <queue>
#include <set>
#include <string>
#include <tuple>
#include <utility>
#include <variant>
#include <vector>

namespace LWM {
  // Use JobReturnFutures?
  using JobFuture_t = std::shared_future<JobReturn>;
  using JobFutures_t = std::variant<JobFuture_t, std::vector<JobFuture_t>>;
  using MaybeJobFutures_t = std::optional<JobFuture_t>;

  extern std::atomic<bool> s_interrupted;

  /**
   * Thread-safe registry for tracking active jobs (especially SLURM jobs)
   * to allow for cancellation upon interruption.
   */
  class JobRegistry {
  public:
    static void register_job(const std::string& task_name,
                             const std::string& job_id);
    static void unregister_job(const std::string& task_name);
    static void cancel_all();
    static bool is_empty();

  private:
    static std::mutex m_registry_mut;
    // Set of (task_name, job_id) pairs
    static std::set<std::pair<std::string, std::string>> m_active_jobs;
  };

  class Launcher {
  public:
    Launcher() = default;
    Launcher(const bool& unbuffered_logs) : m_unbuffered_logs(unbuffered_logs) {}
    // Sub-classes may potentially be used via pointers to base so virtual destructor
    virtual ~Launcher() = default;

    virtual JobReturn launch_task(const JobStep& job,
                                  bool is_daq2,
                                  MaybeJobFutures_t wait_for = std::nullopt) = 0;
    virtual JobReturn operator()(const JobStep& job,
                                 bool is_daq2,
                                 MaybeJobFutures_t wait_for = std::nullopt) = 0;
    virtual std::map<std::pair<std::string,HTTP::METHOD>,std::shared_ptr<HTTP::Handler>> get_request_handlers() {
      return m_request_handlers;
    }

    virtual bool use_server() { return m_expects_server; }

  protected:
    std::shared_ptr<spdlog::logger> m_logger = [] {
      if (auto tmp = spdlog::get("LWM:Launcher")) {
        return tmp;
      } else {
        return spdlog::stdout_color_mt("LWM:Launcher");
      }
    }();
    virtual std::shared_ptr<spdlog::logger> logger() { return m_logger; }
    /**
     * Whether this launcher expects the status update to come from the manager's
     * associated REST server (or any other information it needs to run).
     * If this is true, an update callback must be provided which allows the server
     * to set the status.
     */
    bool m_expects_server{true};
    std::map<std::pair<std::string,HTTP::METHOD>,std::shared_ptr<HTTP::Handler>> m_request_handlers;

    /**
     * Checks whether a job can be executed/run based on upstream job return statuses
     * passed along via futures in the `wait_for` argument. This compares the return
     * statuses against the TriggerRule for the job.
     */
    bool can_task_run(const JobStep& job, MaybeJobFutures_t wait_for, std::string& reason);

    /**
     * Whether logging was specified as unbuffered.
     */
    bool m_unbuffered_logs{false};
  };

  /**
   * Functor for request handling of Managed Task status updates.
   * Maintains a map of statuses to managed Task instances which can be accessed
   * by an associated SubprocessLauncher.
   */
  class JsonStatusHandler : public HTTP::JsonHandler {
    // Mutexes and status will be held by the handler
    // The launcher may access them through it however
    friend class SubprocessLauncher;
    // To make the Python code simpler, everything goes through here
    // We'll expose the private stuff to the other Handlers.
    friend class JsonRpcHandler;
    friend class JsonTasksHandler;

  public:
    JsonStatusHandler() = default;
    ~JsonStatusHandler() = default;

    HTTP::Response operator()(const HTTP::Request& request);

  private:
    std::mutex m_status_mut;
    std::map<std::string, std::string> m_status_map;
    std::map<std::string, std::map<std::string, std::string>> m_metadata_map;
    std::map<std::string, std::queue<std::string>> m_rpc_queues;

    void update_running_splits(JobStepSplits* splits, std::string& managed_task_name);
    // Should be protected behind the m_status_mut as well.
    std::map<std::string,JobStepSplits*> m_splits_map;
    void remove_running_splits(std::string& managed_task_name);
  };

  /**
   * Functor for request handling of Managed Task logs.
   * Maintains a map of statuses to managed Task instances which can be accessed
   * by an associated SubprocessLauncher.
   */
  class JsonLogHandler : public HTTP::JsonHandler {
    // Mutexes will be held by the handler
    // The launcher may access them through it however
    friend class SubprocessLauncher;

  public:
    JsonLogHandler() = default;
    JsonLogHandler(bool unbuffered_logs) : m_unbuffered_logs(unbuffered_logs) {}
    ~JsonLogHandler() = default;

    HTTP::Response operator()(const HTTP::Request& request);

  private:
    std::mutex m_log_mut;
    std::map<std::string, std::string> m_log_map;

    std::shared_ptr<spdlog::logger> m_logger = [] {
      if (auto tmp = spdlog::get("LWM:JsonLogHandler")) {
        return tmp;
      } else {
        return spdlog::stdout_color_mt("LWM:JsonLogHandler");
      }
    }();
    bool m_unbuffered_logs{false};
  };

  /**
   * Handler for listing discovered tasks and their current status/metadata.
   */
  class JsonTasksHandler : public HTTP::JsonHandler {
    friend class SubprocessLauncher;

  public:
    JsonTasksHandler(std::shared_ptr<JsonStatusHandler> status_handler)
      : m_status_handler(status_handler)
    {}
    ~JsonTasksHandler() = default;

    HTTP::Response operator()(const HTTP::Request& request) override;

  private:
    std::shared_ptr<JsonStatusHandler> m_status_handler;

    std::shared_ptr<spdlog::logger> m_logger = [] {
      if (auto tmp = spdlog::get("LWM:JsonTasksHandler")) {
        return tmp;
      } else {
        return spdlog::stdout_color_mt("LWM:JsonTasksHandler");
      }
    }();
    bool m_unbuffered_logs{false};
  };

  /**
   * Handler for sending and receiving RPC messages.
   */
  class JsonRpcHandler : public HTTP::JsonHandler {
    friend class SubprocessLauncher;

  public:
    JsonRpcHandler(std::shared_ptr<JsonStatusHandler> status_handler)
      : m_status_handler(status_handler)
    {}
    ~JsonRpcHandler() = default;

    HTTP::Response operator()(const HTTP::Request& request) override;

  private:
    std::shared_ptr<JsonStatusHandler> m_status_handler;

    std::shared_ptr<spdlog::logger> m_logger = [] {
      if (auto tmp = spdlog::get("LWM:JsonRpcHandler")) {
        return tmp;
      } else {
        return spdlog::stdout_color_mt("LWM:JsonRpcHandler");
      }
    }();
    bool m_unbuffered_logs{false};
  };

  /**
   * Launcher implementation which runs the job it is passed via a subprocess
   * using popen (or another subprocess mechanism with pipes).
   *
   * No sub-classes can override the actual launch function. Specialization is provided
   * by overriding the `prepare_parameter_str` function which creates the command to be
   * invoked via popen/other subprocess launch mechanisms.
   */
  class SubprocessLauncher : public Launcher {

  public:
    SubprocessLauncher() : Launcher() {
      m_request_handlers[std::make_pair("/status", HTTP::METHOD::POST)] = m_status_handler;
      m_request_handlers[std::make_pair("/log", HTTP::METHOD::POST)] = m_log_handler;
      m_request_handlers[std::make_pair("/tasks", HTTP::METHOD::GET)] = m_tasks_handler;
      m_request_handlers[std::make_pair("/rpc", HTTP::METHOD::GET)] = m_rpc_handler;
      m_request_handlers[std::make_pair("/rpc", HTTP::METHOD::POST)] = m_rpc_handler;
    }
    SubprocessLauncher(const bool& unbuffered_logs) : Launcher(unbuffered_logs) {
      m_request_handlers[std::make_pair("/status", HTTP::METHOD::POST)] = m_status_handler;
      m_request_handlers[std::make_pair("/log", HTTP::METHOD::POST)] = m_log_handler;
      m_request_handlers[std::make_pair("/tasks", HTTP::METHOD::GET)] = m_tasks_handler;
      m_request_handlers[std::make_pair("/rpc", HTTP::METHOD::GET)] = m_rpc_handler;
      m_request_handlers[std::make_pair("/rpc", HTTP::METHOD::POST)] = m_rpc_handler;
    }
    JobReturn launch_task(const JobStep& job,
                          bool is_daq2,
                          MaybeJobFutures_t wait_for = std::nullopt) final;

    JobReturn operator()(const JobStep& job,
                         bool is_daq2,
                         MaybeJobFutures_t wait_for = std::nullopt) final;

  protected:
    // Sub-class must override `prepare_parameter_str`
    virtual std::string prepare_launch_cmd(const JobStep& job, bool is_daq2) = 0;
    std::pair<std::string, int> run_subprocess_log(const std::string& cmd,
                                                   bool return_output = false);
    virtual void update_log(std::string& log, std::string& jobid) {}

    std::shared_ptr<JsonStatusHandler> m_status_handler = std::make_shared<JsonStatusHandler>();
    std::shared_ptr<JsonLogHandler> m_log_handler = std::make_shared<JsonLogHandler>(m_unbuffered_logs);
    std::shared_ptr<JsonTasksHandler> m_tasks_handler = std::make_shared<JsonTasksHandler>(m_status_handler);
    std::shared_ptr<JsonRpcHandler> m_rpc_handler = std::make_shared<JsonRpcHandler>(m_status_handler);

    std::shared_ptr<spdlog::logger> m_logger = [] {
      if (auto tmp = spdlog::get("LWM:SubprocessLauncher")) {
        return tmp;
      } else {
        return spdlog::stdout_color_mt("LWM:SubprocessLauncher");
      }
    }();
    virtual std::shared_ptr<spdlog::logger> logger() override { return m_logger; }
  };

  /**
   * Invoke the job step by calling `python ...` directly.
   */
  class PythonLauncher : public SubprocessLauncher {
  public:
    PythonLauncher() : SubprocessLauncher(){}
    PythonLauncher(const bool& unbuffered_logs) : SubprocessLauncher(unbuffered_logs) {}
  protected:
    std::string prepare_launch_cmd(const JobStep& job, bool is_daq2) override;
    std::shared_ptr<spdlog::logger> m_logger = [] {
      if (auto tmp = spdlog::get("LWM:PythonLauncher")) {
        return tmp;
      } else {
        return spdlog::stdout_color_mt("LWM:PythonLauncher");
      }
    }();
    virtual std::shared_ptr<spdlog::logger> logger() override { return m_logger; }
  };

  /**
   * Invoke the job step by submitting a shell script for `sbatch` to be managed
   * as a SLURM job.
   */
  class SlurmLauncher : public SubprocessLauncher {
  public:
    SlurmLauncher() : SubprocessLauncher() {}
    SlurmLauncher(const bool& unbuffered_logs)
        : SubprocessLauncher(unbuffered_logs) {}

  protected:
    void update_log(std::string& log, std::string& jobid) override;
    std::string prepare_launch_cmd(const JobStep& job, bool is_daq2) override;
    std::shared_ptr<spdlog::logger> m_logger = [] {
      if (auto tmp = spdlog::get("LWM:SlurmLauncher")) {
        return tmp;
      } else {
        return spdlog::stdout_color_mt("LWM:SlurmLauncher");
      }
    }();
    virtual std::shared_ptr<spdlog::logger> logger() override { return m_logger; }
  };


  enum class LauncherType {
    PythonLauncherType = 0,
    SlurmLauncherType = 1
  };
} // namespace LWM

#endif

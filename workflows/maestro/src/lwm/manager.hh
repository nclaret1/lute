#ifndef LWM_MANAGER_HH
#define LWM_MANAGER_HH

#include "job.hh"
#include "launcher.hh"
#include "../parallel/threadpool.hh"
#include "../server/server.hh"

#include "spdlog/sinks/stdout_color_sinks.h"

#include <memory>
#include <string>
#include <variant>
#include <vector>

namespace LWM {
  using ParallelJobSteps = std::vector<JobStep>;
  using WfDefinition = ParallelJobSteps;
  //using WfDefinition = std::variant<JobStep, ParallelJobSteps>;

  class ManagerParameters {
  public:
    /**
     * Number of threads for the manager. This should probably match the number
     * of Managed Task's that will run concurrently.
     */
    unsigned num_manager_threads{2};
    /**
     * Number of threads for the server process. This probably doesn't need to be
     * very high unless you expect a lot of HTTP traffic.
     */
    unsigned num_server_threads{2};
    /**
     * Whether to print logs immediately or only at the end of each JobStep by
     * pulling them from the log file.
     */
    bool unbuffered_logs{false};

    /**
     * HTTP server host IP (usually 0.0.0.0)
     */
    std::string host{"0.0.0.0"};
    /**
     * HTTP server port.
     */
    std::uint16_t port{8080};
    /**
     * What kind of job launching to employ (SLURM, Python, etc.)
     */
    LauncherType launch_type{LauncherType::PythonLauncherType};
    /**
     * Whether the experiment/workflow is LCLS2. This affects the base environment used.
     */
    bool is_daq2{false};
    /**
     * What the run type is for this experiment run.
     */
    std::string run_type{""};
  };

  class Manager {
  public:
    Manager() = delete;
    Manager(const ManagerParameters& params);
    Manager(const std::string& host, std::uint16_t port, LauncherType launch_type);
    Manager(WfDefinition wf_defn, LauncherType launcher_type);

    void queue_workflow(const WfDefinition& wf_defn);
    std::string run_workflow();

  private:
    std::string m_host;
    std::uint16_t m_port;
    ManagerParameters m_params;

    HTTP::Server m_server;
    std::unique_ptr<Launcher> m_launcher;
    ThreadPool m_job_pool;

    std::vector<WfDefinition> m_workflows;

    void recurse_workflow(const WfDefinition& wf, MaybeJobFutures_t wait_for);
    std::shared_ptr<std::vector<std::shared_future<JobReturn>>> m_all_futures;

    std::shared_ptr<spdlog::logger> m_logger;
  };

} // namespace LWM
#endif

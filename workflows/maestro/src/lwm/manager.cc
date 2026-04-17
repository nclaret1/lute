#include "manager.hh"
#include "job.hh"
#include "launcher.hh"

#include "spdlog/cfg/env.h"
#include "spdlog/sinks/stdout_color_sinks.h"

#include <future>
#include <iostream>
#include <memory>
#include <optional>
#include <string>
#include <vector>

namespace LWM {
  Manager::Manager(const ManagerParameters& params)
    : m_params(params)
    , m_server(HTTP::Server(params.host, params.port))
    , m_job_pool(params.num_manager_threads)
    , m_logger([] {
      if (auto tmp = spdlog::get("LWM:Manager")) {
        return tmp;
      } else {
        return spdlog::stdout_color_mt("LWM:Manager");
      }
    }())
  {
    spdlog::cfg::load_env_levels("LUTE_MAESTRO_LOG_LEVEL");
    std::string msg{"Running workflows with "};
    switch (params.launch_type) {
    case LauncherType::PythonLauncherType: {
      m_launcher = std::move(std::make_unique<PythonLauncher>(params.unbuffered_logs));
      msg += "PythonLauncher";
      break;
    }
    case LauncherType::SlurmLauncherType: {
      m_launcher = std::move(std::make_unique<SlurmLauncher>(params.unbuffered_logs));
      msg += "SlurmLauncher.";
      break;
    }
    }
    m_all_futures =
        std::make_shared<std::vector<std::shared_future<JobReturn>>>();
    m_logger->info(msg);
    if (m_launcher->use_server()) {
      auto request_handlers = m_launcher->get_request_handlers();
      for (auto [route_method_pair, handler] : request_handlers) {
        m_server.add_request_handler(route_method_pair.first,
                                     route_method_pair.second, handler);
      }
    }
  }
  Manager::Manager(WfDefinition wf_defn, LauncherType launch_type)
    : m_server(HTTP::Server("0.0.0.0", 8080))
    , m_job_pool(5)
    , m_logger(spdlog::stdout_color_mt("LWM:Manager"))
  {
    spdlog::cfg::load_env_levels("LUTE_MAESTRO_LOG_LEVEL");
    std::string msg{"Running workflows with "};
    switch (launch_type) {
    case LauncherType::PythonLauncherType: {
      m_launcher = std::move(std::make_unique<PythonLauncher>());
      msg += "PythonLauncher";
      break;
    }
    case LauncherType::SlurmLauncherType: {
      m_launcher = std::move(std::make_unique<SlurmLauncher>());
      msg += "SlurmLauncher.";
      break;
    }
    }
    m_all_futures =
        std::make_shared<std::vector<std::shared_future<JobReturn>>>();
    m_logger->info(msg);
    if (m_launcher->use_server()) {
      auto request_handlers = m_launcher->get_request_handlers();
      for (auto [route_method_pair, handler] : request_handlers) {
        m_server.add_request_handler(route_method_pair.first,
                                     route_method_pair.second, handler);
      }
    }
  }

  Manager::Manager(const std::string& host, std::uint16_t port, LauncherType launch_type)
    : m_host(host)
    , m_port(port)
    , m_server(HTTP::Server(m_host, m_port))
    , m_job_pool(2)
    , m_logger(spdlog::stdout_color_mt("LWM:Manager"))
  {
    spdlog::cfg::load_env_levels("LUTE_MAESTRO_LOG_LEVEL");
    std::string msg{"Running workflows with "};
    switch (launch_type) {
    case LauncherType::PythonLauncherType: {
      m_launcher = std::move(std::make_unique<PythonLauncher>());
      msg += "PythonLauncher";
      break;
    }
    case LauncherType::SlurmLauncherType: {
      m_launcher = std::move(std::make_unique<SlurmLauncher>());
      msg += "SlurmLauncher.";
      break;
    }
    }

    if (m_launcher->use_server()) {
      auto request_handlers = m_launcher->get_request_handlers();
      for (auto [route_method_pair, handler] : request_handlers) {
        m_server.add_request_handler(route_method_pair.first, route_method_pair.second, handler);
      }
    }

    m_all_futures = std::make_shared<std::vector<std::shared_future<JobReturn>>>();
    m_logger->info(msg);
  }

  void Manager::queue_workflow(const WfDefinition& wf_defn) {
    m_workflows.push_back(wf_defn);
  }

  void Manager::recurse_workflow(const WfDefinition& wf, MaybeJobFutures_t wait_for=std::nullopt) {
    auto launch_func = [&](const JobStep &job,
                           bool is_daq2,
                           MaybeJobFutures_t wait_for = std::nullopt) -> JobReturn {
      // Wrap the launch function in a lambda to avoid making Launcher::launch_task
      // a static function
      return m_launcher->launch_task(job, is_daq2, wait_for);
    };

    for (const auto& step : wf) {
      auto next_wait_for = std::shared_future<JobReturn>(m_job_pool.enqueue(launch_func,
                                                                            step,
                                                                            m_params.is_daq2,
                                                                            wait_for));
      m_all_futures->push_back(next_wait_for);
      if (!step.next.empty()) {
        recurse_workflow(step.next, std::optional<decltype(next_wait_for)>(next_wait_for));
      }
    }
  }

  std::string Manager::run_workflow() {
    m_server.start();
    m_logger->info("Beginning workflow.");
    // m_workflows = Vector of Parallel JobSteps
    for (const auto& wf : m_workflows) {
      recurse_workflow(wf, std::nullopt);
      // wf = vector of JobSteps
    }
    int n_complete{0};
    int n_success{0};
    bool cancellation_triggered{false};
    while (true) {
      if (m_all_futures->size() == 0) {
        break;
      }
      if (s_interrupted && !cancellation_triggered) {
        m_logger->warn("Interruption detected! Cancelling active jobs...");
        JobRegistry::cancel_all();
        cancellation_triggered = true;
      }
      if (s_interrupted) {
        m_logger->warn("Interruption detected! Waiting for active tasks to shut down...");
      }
      auto& futures = *m_all_futures;
      for (auto return_it = futures.begin(); return_it != futures.end();) {
        if (return_it->wait_for(std::chrono::milliseconds(200)) == std::future_status::ready) {
          ++n_complete;
          auto& [task_name, status, logfile, splits] = return_it->get();
          std::string msg = "Providing logs for " + task_name + " [Exited as: " + status
            + "]\n" +
            "-----------------------------------------------------------------------\n";
          if (status == "COMPLETED") {
            n_success++;
            m_logger->info(msg);
            using namespace std::literals;
            const auto pending_time = splits.running_point - splits.launch_point;
            const auto running_time = splits.end_point - splits.running_point;
            if (!m_params.unbuffered_logs) {
              std::cout << logfile << std::endl;
            }
            std::cout << "Time " << task_name << " spent: " << std::endl
                      << "- Pending: " << pending_time / 1s << " s" << std::endl
                      << "- Running: " << running_time / 1s << " s"
                      << std::endl;
          } else {
            m_logger->error(msg);
            if (!m_params.unbuffered_logs) {
              std::cout << logfile << std::endl;
            }
          }
          return_it = futures.erase(return_it);
        } else {
          ++return_it;
          m_logger->trace("Waiting on Managed Task completion.");
        }
      }
    }
    m_server.stop();
    if (n_complete == n_success) {
      m_logger->info("Exiting after workflow completed successfully.");
      return "COMPLETED";
    } else {
      m_logger->error(
        "Exiting after workflow failed! "
        "[" + std::to_string(n_success) + "/" + std::to_string(n_complete) + " succeeded]"
      );
      return "FAILED";
    }
  }
} // namespace LWM

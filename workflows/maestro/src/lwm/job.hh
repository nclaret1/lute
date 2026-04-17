#ifndef LWM_JOB_HH
#define LWM_JOB_HH

#include <chrono>
#include <string>
#include <vector>

namespace LWM {
  enum class TriggerRule {
    ALL_SUCCESS=0, // All previous job steps must exit successfully
    ANY_SUCCESS=1, // Run as soon as any previous job step exits successfully
    ALL_COMPLETED=2, // Run when all previous job steps complete, successful or not

    ALL_FAILED=3,
    ANY_FAILED=4,

    ALWAYS=5
  };

  std::string rule_to_string(TriggerRule& rule);

  class JobParameters {
  public:
    std::string lute_location;
    std::string executable_subdir;
    std::string config_file;
    bool debug;
  };

  class JobStep {
  public:
    JobStep(std::string _task_name,
            TriggerRule _trigger_rule,
            JobParameters _parameters,
            std::string _extra_parameters,
            std::vector<JobStep> _next)
      : managed_task_name(_task_name)
      , trigger_rule(_trigger_rule)
      , parameters(_parameters)
      , extra_parameters(_extra_parameters)
      , next(_next)
    {}

    std::string managed_task_name;
    TriggerRule trigger_rule;
    JobParameters parameters;
    std::string extra_parameters;
    std::vector<JobStep> next;
  };

  /**
   * A simple struct that holds the timing information for an individual
   * JobStep. The time the Launcher submits the step, the time the JobStep is
   * confirmed to be running, and the time the JobStep exits are recorded.
   */
  class JobStepSplits {
  public:
    std::chrono::time_point<std::chrono::steady_clock>
        launch_point; ///< Time Launcher submits the JobStep
    std::chrono::time_point<std::chrono::steady_clock>
        running_point; ///< Time JobStep reports it is running
    std::chrono::time_point<std::chrono::steady_clock>
        end_point; ///< Time JobStep completes
  };

  class JobReturn {
  public:
    JobReturn(std::string _name,
              std::string _status,
              std::string _log,
              JobStepSplits _splits)
      : managed_task_name(_name)
      , status(_status)
      , log(_log)
      , splits(_splits)
    {}

    // Specify move since it may be returned from function
    JobReturn(JobReturn&& other) noexcept
      : managed_task_name(std::move(other.managed_task_name))
      , status(std::move(other.status))
      , log(std::move(other.log))
      , splits(std::move(other.splits))
    {}
    std::string managed_task_name;
    std::string status;
    std::string log;
    JobStepSplits splits;
  };

  class JobStepInfo {
  public:
    JobStep job;      ///< The job
    std::string host; ///< Host this job is running on/ran on
  };
};
#endif

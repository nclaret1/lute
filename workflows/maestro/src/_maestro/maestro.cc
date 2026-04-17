#include "../lwm/job.hh"
#include "../lwm/launcher.hh"
#include "../lwm/manager.hh"
#include "../server/handler.hh"

//#include <pybind11/chrono.h>
//#include <pybind11/functional.h>
#include <csignal>
#include <cstdint>
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <iostream>
#include <string>

namespace py = pybind11;

void signal_handler(int signal) {
  if (signal == SIGINT) {
    LWM::s_interrupted = true;
  }
}

std::string run_workflow(LWM::WfDefinition wf_defn,
                         LWM::ManagerParameters manager_params) {
  std::signal(SIGINT, signal_handler);
  LWM::Manager manager = LWM::Manager(manager_params);
  manager.queue_workflow(wf_defn);

  std::string status = manager.run_workflow();
  std::signal(SIGINT, SIG_DFL);
  return status;
}

PYBIND11_MODULE(_maestro, m, py::mod_gil_not_used()) {
  m.doc() = "Maestro Python bindings for LWM C++. "
    "Refer to LWM c++ code and maestro Python code for more information.";

  py::enum_<LWM::TriggerRule>(m, "TriggerRule")
    .value("ALL_SUCCESS", LWM::TriggerRule::ALL_SUCCESS)
    .value("ANY_SUCCESS", LWM::TriggerRule::ANY_SUCCESS)
    .value("ALL_COMPLETED", LWM::TriggerRule::ALL_COMPLETED)
    .value("ALL_FAILED", LWM::TriggerRule::ALL_FAILED)
    .value("ANY_FAILED", LWM::TriggerRule::ANY_FAILED)
    .value("ALWAYS", LWM::TriggerRule::ALWAYS);

  py::class_<LWM::JobParameters>(m, "JobParameters")
    .def(py::init<std::string, std::string, std::string, bool>())
    .def_readwrite("lute_location", &LWM::JobParameters::lute_location)
    .def_readwrite("config_file", &LWM::JobParameters::config_file)
    .def_readwrite("executable_subdir", &LWM::JobParameters::executable_subdir)
    .def_readwrite("debug", &LWM::JobParameters::debug);

  py::class_<LWM::JobStep>(m, "JobStep")
    .def(py::init<
           std::string,                 // Managed Task Name
           LWM::TriggerRule,            // Job step trigger rule
           LWM::JobParameters,          // General parameters (LUTE location, etc.)
           std::string,                 // Extra parameter string (e.g. SLURM parameters)
           std::vector<LWM::JobStep>>() // Subsequent JobSteps
         )
    .def_readwrite("managed_task_name", &LWM::JobStep::managed_task_name)
    .def_readwrite("trigger_rule", &LWM::JobStep::trigger_rule)
    .def_readwrite("parameters", &LWM::JobStep::parameters)
    .def_readwrite("extra_parameters", &LWM::JobStep::extra_parameters)
    .def_readwrite("next", &LWM::JobStep::next);

  py::enum_<LWM::LauncherType>(m, "LauncherType")
    .value("PythonLauncherType", LWM::LauncherType::PythonLauncherType)
    .value("SlurmLauncherType", LWM::LauncherType::SlurmLauncherType);

  py::class_<LWM::ManagerParameters>(m, "ManagerParameters")
    .def(py::init<
         unsigned,            // The number of manager threads
         unsigned,            // The number of HTTP server threads
         bool,                // Whether to print logs immediately or only after JobStep ends
         std::string,         // HTTP server ip (0.0.0.0 for all interfaces)
         std::uint16_t,       // HTTP server port
         LWM::LauncherType,   // JobStep launching mechanism (e.g. Python or SLURM)
         bool,                // Whether the experiment/workflow is LCLS2
         std::string>()       // The run type.
     )
    .def_readwrite("num_manager_threads", &LWM::ManagerParameters::num_manager_threads)
    .def_readwrite("config_file", &LWM::ManagerParameters::num_server_threads)
    .def_readwrite("debug", &LWM::ManagerParameters::unbuffered_logs)
    .def_readwrite("host", &LWM::ManagerParameters::host)
    .def_readwrite("port", &LWM::ManagerParameters::port)
    .def_readwrite("launch_type", &LWM::ManagerParameters::launch_type)
    .def_readwrite("is_daq2", &LWM::ManagerParameters::is_daq2)
    .def_readwrite("run_type", &LWM::ManagerParameters::run_type);

  m.def("run_workflow", &run_workflow, "Run a LUTE workflow using maestro.");
}

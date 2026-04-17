#include "job.hh"

#include <stdexcept>
#include <string>

namespace LWM {
  std::string rule_to_string(TriggerRule& rule) {
    switch (rule) {
    case TriggerRule::ALL_SUCCESS:
      return "ALL_SUCCESS";
    case TriggerRule::ANY_SUCCESS:
      return "ANY_SUCCESS";
    case TriggerRule::ALL_COMPLETED:
      return "ALL_COMPLETED";
    case TriggerRule::ALL_FAILED:
      return "ALL_FAILED";
    case TriggerRule::ANY_FAILED:
      return "ANY_FAILED";
    case TriggerRule::ALWAYS:
      return "ALWAYS";
    default:
      throw std::runtime_error("Unrecognized TriggerRule.");
    }
  }
} // namespace LWM

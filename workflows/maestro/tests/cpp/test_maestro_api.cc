#include "http.hh"
#include "launcher.hh"
#include <gtest/gtest.h>
#include <iostream>
#include <map>
#include <memory>
#include <string>

using namespace LWM;
using namespace HTTP;

class MaestroAPITest : public ::testing::Test {
protected:
  std::shared_ptr<JsonStatusHandler> status_handler;
  std::shared_ptr<JsonTasksHandler> tasks_handler;
  std::shared_ptr<JsonRpcHandler> rpc_handler;

  void SetUp() override {
    status_handler = std::make_shared<JsonStatusHandler>();
    tasks_handler = std::make_shared<JsonTasksHandler>(status_handler);
    rpc_handler = std::make_shared<JsonRpcHandler>(status_handler);
  }
};

TEST_F(MaestroAPITest, StatusUpdateBasic) {
  // Basic status update
  std::string content = "{\"managed_task\": \"test_task\", \"status\": \"STARTED\"}";
  Request req;
  req.set_method(METHOD::POST);
  req.set_url("/status");
  req.set_content(content);

  Response res = (*status_handler)(req);
  EXPECT_EQ(res.status_code(), CODE::OK);
  EXPECT_EQ(res.content(), "Status received.");
}

TEST_F(MaestroAPITest, StatusUpdateExtended) {
  // Extended metadata update
  std::string content = "{\"managed_task\": \"test_task\", \"status\": \"RUNNING\", \"step\": "
                        "\"1\", \"custom_key\": \"custom_val\"}";
  Request req;
  req.set_method(METHOD::POST);
  req.set_url("/status");
  req.set_content(content);

  Response res = (*status_handler)(req);
  EXPECT_EQ(res.status_code(), CODE::OK);

  // Now check if tasks handler shows it
  Request tasks_req;
  tasks_req.set_method(METHOD::GET);
  tasks_req.set_url("/tasks");

  Response tasks_res = (*tasks_handler)(tasks_req);
  EXPECT_EQ(tasks_res.status_code(), CODE::OK);

  std::string tasks_content = tasks_res.content();
  // Use substrings without assuming spaces after colons
  EXPECT_NE(tasks_content.find("\"name\":\"test_task\""), std::string::npos)
      << "Output was: " << tasks_content;
  EXPECT_NE(tasks_content.find("\"status\":\"RUNNING\""), std::string::npos);
  EXPECT_NE(tasks_content.find("\"step\":\"1\""), std::string::npos);
  EXPECT_NE(tasks_content.find("\"custom_key\":\"custom_val\""), std::string::npos);
}

TEST_F(MaestroAPITest, TasksListEmpty) {
  Request req;
  req.set_method(METHOD::GET);
  req.set_url("/tasks");

  Response res = (*tasks_handler)(req);
  EXPECT_EQ(res.status_code(), CODE::OK);
  EXPECT_EQ(res.content(), "{ \"managed_tasks\": [] }");
}

TEST_F(MaestroAPITest, RPCOperations) {
  // POST to queue a message
  std::string post_content = "{\"target\": \"target_task\", \"message\": \"hello_rpc\"}";
  Request post_req;
  post_req.set_method(METHOD::POST);
  post_req.set_url("/rpc");
  post_req.set_content(post_content);

  Response post_res = (*rpc_handler)(post_req);
  EXPECT_EQ(post_res.status_code(), CODE::OK);
  EXPECT_EQ(post_res.content(), "Message queued.");

  // GET with query param to retrieve
  Request get_req_param;
  get_req_param.set_method(METHOD::GET);
  get_req_param.set_url("/rpc?task=target_task");

  Response get_res_param = (*rpc_handler)(get_req_param);
  EXPECT_EQ(get_res_param.status_code(), CODE::OK);
  EXPECT_NE(get_res_param.content().find("\"message\": \"hello_rpc\""), std::string::npos)
      << "Output was: " << get_res_param.content();

  // Queue another message for POST body test
  std::string post_content2 = "{\"target\": \"target_task\", \"message\": \"hello_rpc_2\"}";
  Request post_req2;
  post_req2.set_method(METHOD::POST);
  post_req2.set_url("/rpc");
  post_req2.set_content(post_content2);
  (*rpc_handler)(post_req2);

  // GET with POST body to retrieve
  std::string get_body = "{\"task\": \"target_task\"}";
  Request get_req_body;
  get_req_body.set_method(METHOD::GET);
  get_req_body.set_url("/rpc");
  get_req_body.set_content(get_body);

  Response get_res_body = (*rpc_handler)(get_req_body);
  EXPECT_EQ(get_res_body.status_code(), CODE::OK);
  EXPECT_NE(get_res_body.content().find("\"message\": \"hello_rpc_2\""), std::string::npos);

  // GET for non-existent message
  Request get_req_empty;
  get_req_empty.set_method(METHOD::GET);
  get_req_empty.set_url("/rpc?task=target_task");

  Response get_res_empty = (*rpc_handler)(get_req_empty);
  EXPECT_EQ(get_res_empty.status_code(), CODE::OK);
  EXPECT_NE(get_res_empty.content().find("\"message\": null"), std::string::npos);
}

TEST_F(MaestroAPITest, RPCBadRequest) {
  // Missing target/message
  std::string bad_content = "{\"target\": \"only_target\"}";
  Request req;
  req.set_method(METHOD::POST);
  req.set_url("/rpc");
  req.set_content(bad_content);

  Response res = (*rpc_handler)(req);
  EXPECT_EQ(res.status_code(), CODE::BadRequest);
}

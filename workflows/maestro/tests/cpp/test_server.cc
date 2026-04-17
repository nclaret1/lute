#include "server.hh"

#include <gtest/gtest.h>

#include <arpa/inet.h>
#include <chrono>
#include <netinet/in.h>
#include <string>
#include <sys/socket.h>
#include <thread>

using namespace HTTP;
using namespace std::string_literals;

class MockHandler : public Handler {
public:
  Response operator()(const Request& request) override {
    Response res(CODE::OK);
    res.set_content("Echo: " + request.content());
    return res;
  }
};

TEST(ServerTest, StartStop) {
  // Use a high port to avoid permission issues
  Server server("127.0.0.1", 18888);

  server.start();
  EXPECT_TRUE(server.running());
  EXPECT_EQ(server.port(), 18888);
  EXPECT_EQ(server.host(), "127.0.0.1");

  server.stop();
  EXPECT_FALSE(server.running());
}

TEST(ServerTest, HandleRequest) {
  Server server("127.0.0.1", 18889);
  server.add_request_handler("/echo",
                             METHOD::POST,
                             std::make_shared<MockHandler>());
  server.start();

  // Small sleep to ensure server is up
  std::this_thread::sleep_for(std::chrono::milliseconds(100));

  // Simple client to test connection
  int sock = socket(AF_INET, SOCK_STREAM, 0);
  sockaddr_in serv_addr;
  serv_addr.sin_family = AF_INET;
  serv_addr.sin_port = htons(18889);
  inet_pton(AF_INET, "127.0.0.1", &serv_addr.sin_addr);

  ASSERT_GE(connect(sock, (struct sockaddr *)&serv_addr, sizeof(serv_addr)), 0);

  std::string request = "POST /echo HTTP/1.1\r\nContent-Length: 5\r\n\r\nHello";
  send(sock, request.c_str(), request.length(), 0);

  std::string response;
  char read_buf[1024];
  while (true) {
    ssize_t n = read(sock, read_buf, sizeof(read_buf));
    if (n <= 0)
      break;
    response.append(read_buf, n);
    if (response.find("\r\n\r\n") != std::string::npos) {
      // Simple check: if we have some content after headers, we might be done
      // Real HTTP client would check Content-Length, but this is a test mock
      if (response.size() > response.find("\r\n\r\n") + 4 + 5)
        break;
    }
  }

  EXPECT_NE(response.find("200 OK"), std::string::npos);
  EXPECT_NE(response.find("Echo: Hello"), std::string::npos);

  close(sock);
  server.stop();
}

TEST(ServerTest, LargeLogRequest) {
  Server server("127.0.0.1", 18890);
  server.add_request_handler("/log",
                             METHOD::POST,
                             std::make_shared<MockHandler>());
  server.start();

  // Small sleep to ensure server is up
  std::this_thread::sleep_for(std::chrono::milliseconds(100));

  int sock = socket(AF_INET, SOCK_STREAM, 0);
  sockaddr_in serv_addr;
  serv_addr.sin_family = AF_INET;
  serv_addr.sin_port = htons(18890);
  inet_pton(AF_INET, "127.0.0.1", &serv_addr.sin_addr);

  ASSERT_GE(connect(sock, (struct sockaddr*)&serv_addr, sizeof(serv_addr)), 0);

  // This caused a lot of issues for JSON parsing...
  std::string log_text =
      R"(([sdfmilan251:1167543] osc_ucx_component.c:369  Error: OSC UCX component priority set inside component query failed \n \n[sdfmilan251:1167542] osc_ucx_component.c:369  Error: OSC UCX component priority set inside component query failed \n \n[sdfmilan251:1167609] osc_ucx_component.c:369  Error: OSC UCX component priority set inside component query failed \n \n[sdfmilan251:1167581] osc_ucx_component.c:369  Error: OSC UCX component priority set inside component query failed \n \n[sdfmilan251:1167547] osc_ucx_component.c:369  Error: OSC UCX component priority set inside component query failed \n \n[sdfmilan251:1167576] osc_ucx_component.c:369  Error: OSC UCX component priority set inside component query failed \n \n[sdfmilan251:1167571] osc_ucx_component.c:369  Error: OSC UCX component priority set inside component query failed \n \n[sdfmilan251:1167564] osc_ucx_component.c:369  Error: OSC UCX component priority set inside component query failed \n \n[sdfmilan251:1167605] osc_ucx_component.c:369  Error: OSC UCX component priority set inside component query failed \n \n[sdfmilan251:1167598] osc_ucx_component.c:369  Error: OSC UCX component priority set inside component query failed \n \n[sdfmilan251:1167555] osc_ucx_component.c:369  Error: OSC UCX component priority set inside component query failed \n \n[sdfmilan251:1167586] osc_ucx_component.c:369  Error: OSC UCX component priority set inside component query failed \n \n[sdfmilan251:1167596] osc_ucx_component.c:369  Error: OSC UCX component priority set inside component query failed \n \n[sdfmilan251:1167557] osc_ucx_component.c:369  Error: OSC UCX component priority set inside component query failed \n \n[sdfmilan251:1167560] osc_ucx_component.c:369  Error: OSC UCX component priority set inside component query failed \n \n[sdfmilan251:1167582] osc_ucx_component.c:369  Error: OSC UCX component priority set inside component query failed \n \n[sdfmilan251:1167588] osc_ucx_component.c:369  Error: OSC UCX component priority set inside component query failed \n \n[sdfmilan251:1167573] osc_ucx_component.c:369  Error: OSC UCX component priority set inside component query failed \n \n[sdfmilan251:1167585] osc_ucx_component.c:369  Error: OSC UCX component priority set inside component query failed \n \n[sdfmilan251:1167563] osc_ucx_component.c:369  Error: OSC UCX component priority set inside component query failed \n \n[sdfmilan251:1167607] osc_ucx_component.c:369  Error: OSC UCX component priority set inside component query failed \n \n[sdfmilan251:1167595] osc_ucx_component.c:369  Error: OSC UCX component priority set inside component query failed \n \n[sdfmilan251:1167602] osc_ucx_component.c:369  Error: OSC UCX component priority set inside component query failed \n \n[sdfmilan251:1167603] osc_ucx_component.c:369  Error: OSC UCX component priority set inside component query failed \n \n[sdfmilan251:1167554] osc_ucx_component.c:369  Error: OSC UCX component priority set inside component query failed \n \n[sdfmilan251:1167553] osc_ucx_component.c:369  Error: OSC UCX component priority set inside component query failed \n \n[sdfmilan251:1167592] osc_ucx_component.c:369  Error: OSC UCX component priority set inside component query failed \n \n[sdfmilan251:1167589] osc_ucx_component.c:369  Error: OSC UCX component priority set inside component query failed \n \n[sdfmilan251:1167565] osc_ucx_component.c:369  Error: OSC UCX component priority set inside component query failed \n \n[sdfmilan251:1167550] osc_ucx_component.c:369  Error: OSC UCX component priority set inside component query failed \n \n[sdfmilan251:1167590] osc_ucx_component.c:369  Error: OSC UCX component priority set inside component query failed \n \n[sdfmilan251:1167594] osc_ucx_component.c:369  Error: OSC UCX component priority set inside component query failed \n \n[sdfmilan251:1167561] osc_ucx_component.c:369  Error: OSC UCX component priority set inside component query failed \n \n[sdfmilan251:1167540] osc_ucx_component.c:369  Error: OSC UCX component priority set inside component query failed \n \n[sdfmilan251:1167546] osc_ucx_component.c:369  Error: OSC UCX component priority set inside component query failed \n \n[sdfmilan251:1167562] osc_ucx_component.c:369  Error: OSC UCX component priority set inside component query failed \n \n[sdfmilan251:1167591] osc_ucx_component.c:369  Error: OSC UCX component priority set inside component query failed \n \n[sdfmilan251:1167593] osc_ucx_component.c:369  Error: OSC UCX component priority set inside component query failed \n \n[sdfmilan251:1167575] osc_ucx_component.c:369  Error: OSC UCX component priority set inside component query failed \n \n[sdfmilan251:1167548] osc_ucx_component.c:369  Error: OSC UCX component priority set inside component query failed \n \n[sdfmilan251:1167572] osc_ucx_component.c:369  Error: OSC UCX component priority set inside component query failed \n \n[sdfmilan251:1167599] osc_ucx_component.c:369  Error: OSC UCX component priority set inside component query failed \n \n[sdfmilan251:1167551] osc_ucx_component.c:369  Error: OSC UCX component priority set inside component query failed \n \n[sdfmilan251:1167608] osc_ucx_component.c:369  Error: OSC UCX component priority set inside component query failed \n \n[sdfmilan251:1167606] osc_ucx_component.c:369  Error: OSC UCX component priority set inside component query failed \n \n[sdfmilan251:1167570] osc_ucx_component.c:369  Error: OSC UCX component priority set inside component query failed \n \n[sdfmilan251:1167574] osc_ucx_component.c:369  Error: OSC UCX component priority set inside component query failed \n \n[sdfmilan251:1167600] osc_ucx_component.c:369  Error: OSC UCX component priority set inside component query failed \n \n[sdfmilan251:1167604] osc_ucx_component.c:369  Error: OSC UCX component priority set inside component query failed \n \n[sdfmilan251:1167558] osc_ucx_component.c:369  Error: OSC UCX component priority set inside component query failed \n \n[sdfmilan251:1167583] osc_ucx_component.c:369  Error: OSC UCX component priority set inside component query failed \n \n[sdfmilan251:1167545] osc_ucx_component.c:369  Error: OSC UCX component priority set inside component query failed \n \n[sdfmilan251:1167549] osc_ucx_component.c:369  Error: OSC UCX component priority set inside component query failed \n \n[sdfmilan251:1167579] osc_ucx_component.c:369  Error: OSC UCX component priority set inside component query failed \n \n[sdfmilan251:1167584] osc_ucx_component.c:369  Error: OSC UCX component priority set inside component query failed \n \n[sdfmilan251:1167559] osc_ucx_component.c:369  Error: OSC UCX component priority set inside component query failed \n \n[sdfmilan251:1167552] osc_ucx_component.c:369  Error: OSC UCX component priority set inside component query failed \n \n[sdfmilan251:1167601] osc_ucx_component.c:369  Error: OSC UCX component priority set inside component query failed \n \n[sdfmilan251:1167541] osc_ucx_component.c:369  Error: OSC UCX component priority set inside component query failed \n \n))"s;

  std::string body =
      "{\"managed_task\": \"SmallDataProducer2\", \"message\": \"" + log_text +
      "\"}";
  std::string request =
      "POST /log HTTP/1.1\r\nContent-Length: " + std::to_string(body.length()) +
      "\r\n\r\n" + body;

  send(sock, request.c_str(), request.length(), 0);

  std::string response;
  char read_buf[4096];
  while (true) {
    ssize_t n = read(sock, read_buf, sizeof(read_buf));
    if (n <= 0)
      break;
    response.append(read_buf, n);
    // Exit loop when we have the full expected Echo: body
    if (response.find("Echo: " + body) != std::string::npos)
      break;
    // Or if we get a failure response
    if (response.find("400 BadRequest") != std::string::npos)
      break;
  }

  EXPECT_NE(response.find("200 OK"), std::string::npos);
  EXPECT_NE(response.find("Echo: " + body), std::string::npos);

  close(sock);
  server.stop();
}

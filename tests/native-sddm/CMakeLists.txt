cmake_minimum_required(VERSION 3.16)
project(SddmConversationProofs LANGUAGES CXX)
set(CMAKE_AUTOMOC ON)
set(CMAKE_CXX_STANDARD 17)
find_package(Qt6 REQUIRED COMPONENTS Core Network Test Qml)
find_package(Python3 REQUIRED COMPONENTS Interpreter)
execute_process(COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/prepare.py ${SDDM_SOURCE} ${CMAKE_CURRENT_BINARY_DIR}/generated COMMAND_ERROR_IS_FATAL ANY)
add_executable(native-proofs proofs.cpp
 ${CMAKE_CURRENT_BINARY_DIR}/generated/SocketServer-fixture.cpp
 ${SDDM_SOURCE}/src/daemon/SocketServer.h
 ${SDDM_SOURCE}/src/greeter/GreeterProxy.cpp ${SDDM_SOURCE}/src/greeter/GreeterProxy.h
 ${SDDM_SOURCE}/src/common/SocketWriter.cpp ${SDDM_SOURCE}/src/common/Session.cpp
 ${SDDM_SOURCE}/src/common/Configuration.cpp ${SDDM_SOURCE}/src/common/ConfigReader.cpp)
target_include_directories(native-proofs PRIVATE ${CMAKE_CURRENT_SOURCE_DIR} ${CMAKE_CURRENT_BINARY_DIR}/generated
 ${SDDM_SOURCE}/src/common ${SDDM_SOURCE}/src/greeter ${SDDM_SOURCE}/src/daemon ${SDDM_SOURCE}/src/auth ${SDDM_BUILD}/src/common)
target_link_libraries(native-proofs Qt6::Core Qt6::Network Qt6::Test Qt6::Qml)
enable_testing()
add_test(NAME native-proofs COMMAND native-proofs)

# Compile real QObject/Auth lifetimes: extracted methods cannot model teardown signals.
add_executable(auth-lifecycle-proofs auth-lifecycle.cpp
 ${SDDM_SOURCE}/src/auth/Auth.cpp ${SDDM_SOURCE}/src/auth/Auth.h
 ${SDDM_SOURCE}/src/auth/AuthRequest.cpp ${SDDM_SOURCE}/src/auth/AuthRequest.h
 ${SDDM_SOURCE}/src/auth/AuthPrompt.cpp ${SDDM_SOURCE}/src/auth/AuthPrompt.h
 ${SDDM_SOURCE}/src/common/SafeDataStream.cpp)
target_include_directories(auth-lifecycle-proofs PRIVATE
 ${SDDM_SOURCE}/src/auth ${SDDM_SOURCE}/src/common ${SDDM_BUILD}/src/common)
target_link_libraries(auth-lifecycle-proofs Qt6::Core Qt6::Network Qt6::Test Qt6::Qml)
add_test(NAME auth-lifecycle-proofs COMMAND auth-lifecycle-proofs)

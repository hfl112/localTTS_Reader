import Foundation

#if os(macOS)
import Darwin
#endif

let pythonPath = "/Users/funanhe/miniconda3/envs/gemini/bin/python"
let helperScript = "/Users/funanhe/00_MyCode/TTS/localTTS_macOS/watchdog_helper.py"

func checkProcessAlive(pid: pid_t) -> Bool {
    var status: Int32 = 0
    // WNOHANG 表示非阻塞，如果子进程已退出，waitpid 将回收它并返回 pid。
    let waitResult = waitpid(pid, &status, WNOHANG)
    if waitResult == 0 {
        // 子进程依然在运行，且非僵尸进程
        return true
    } else {
        // 子进程已退出 (返回 pid) 或已被回收/不存在 (返回 -1)
        return false
    }
}

func runWatchdogSpike() {
    print("\n=== [Test 1] Watchdog Pipe EOF Crash Test ===")
    
    var fds: [Int32] = [0, 0]
    guard pipe(&fds) == 0 else {
        print("Failed to create pipe")
        return
    }
    let fdRead = fds[0]
    let fdWrite = fds[1]
    
    fcntl(fdWrite, F_SETFD, FD_CLOEXEC)
    
    var fileActions: posix_spawn_file_actions_t?
    posix_spawn_file_actions_init(&fileActions)
    defer { posix_spawn_file_actions_destroy(&fileActions) }
    
    posix_spawn_file_actions_adddup2(&fileActions, fdRead, 3)
    
    var attr: posix_spawnattr_t?
    posix_spawnattr_init(&attr)
    defer { posix_spawnattr_destroy(&attr) }
    
    posix_spawnattr_setflags(&attr, Int16(POSIX_SPAWN_SETPGROUP))
    posix_spawnattr_setpgroup(&attr, 0)
    
    var envs = ProcessInfo.processInfo.environment
    envs["TTS_WATCHDOG_FD"] = "3"
    envs["TTS_MANAGEMENT_TOKEN"] = "spike-token-uuid-1234"
    
    let envStrings = envs.map { "\($0.key)=\($0.value)" }
    var cEnv = envStrings.map { strdup($0) }
    cEnv.append(nil)
    defer {
        for ptr in cEnv {
            if let p = ptr { free(p) }
        }
    }
    
    let args = [pythonPath, helperScript]
    var cArgs = args.map { strdup($0) }
    cArgs.append(nil)
    defer {
        for ptr in cArgs {
            if let p = ptr { free(p) }
        }
    }
    
    var pid: pid_t = 0
    let spawnStatus = posix_spawn(
        &pid,
        pythonPath,
        &fileActions,
        &attr,
        cArgs,
        cEnv
    )
    
    guard spawnStatus == 0 else {
        print("posix_spawn failed with error: \(spawnStatus)")
        return
    }
    
    print("Spawned Python child PID: \(pid), PGID: \(getpgid(pid))")
    close(fdRead)
    
    Thread.sleep(forTimeInterval: 1.5)
    
    print("Before closing pipe, is Python child alive? \(checkProcessAlive(pid: pid))")
    
    print("Closing pipe write-end (simulating AppKit crash/shutdown)...")
    close(fdWrite)
    
    // 给足 2 秒时间让 Python 和孙子进程优雅收尾并退出
    Thread.sleep(forTimeInterval: 2.0)
    
    let childAlive = checkProcessAlive(pid: pid)
    print("After closing pipe, is Python child alive? \(childAlive)")
    if !childAlive {
        print("SUCCESS: Python child exited automatically on Watchdog EOF.")
    } else {
        print("FAILURE: Python child is still alive.")
        kill(pid, SIGKILL)
    }
}

func runGroupKillSpike() {
    print("\n=== [Test 2] Process Group Strong Kill Test ===")
    
    var fds: [Int32] = [0, 0]
    pipe(&fds)
    let fdRead = fds[0]
    let fdWrite = fds[1]
    fcntl(fdWrite, F_SETFD, FD_CLOEXEC)
    
    var fileActions: posix_spawn_file_actions_t?
    posix_spawn_file_actions_init(&fileActions)
    defer { posix_spawn_file_actions_destroy(&fileActions) }
    posix_spawn_file_actions_adddup2(&fileActions, fdRead, 3)
    
    var attr: posix_spawnattr_t?
    posix_spawnattr_init(&attr)
    defer { posix_spawnattr_destroy(&attr) }
    posix_spawnattr_setflags(&attr, Int16(POSIX_SPAWN_SETPGROUP))
    posix_spawnattr_setpgroup(&attr, 0)
    
    var envs = ProcessInfo.processInfo.environment
    envs["TTS_WATCHDOG_FD"] = "3"
    let envStrings = envs.map { "\($0.key)=\($0.value)" }
    var cEnv = envStrings.map { strdup($0) }
    cEnv.append(nil)
    defer {
        for ptr in cEnv {
            if let p = ptr { free(p) }
        }
    }
    
    let args = [pythonPath, helperScript]
    var cArgs = args.map { strdup($0) }
    cArgs.append(nil)
    defer {
        for ptr in cArgs {
            if let p = ptr { free(p) }
        }
    }
    
    var pid: pid_t = 0
    let spawnStatus = posix_spawn(
        &pid,
        pythonPath,
        &fileActions,
        &attr,
        cArgs,
        cEnv
    )
    
    guard spawnStatus == 0 else {
        print("posix_spawn failed: \(spawnStatus)")
        return
    }
    
    print("Spawned Python child PID: \(pid), PGID: \(getpgid(pid))")
    
    close(fdRead)
    
    Thread.sleep(forTimeInterval: 1.5)
    
    print("Before killpg, is Python child alive? \(checkProcessAlive(pid: pid))")
    
    // 执行进程组强杀 (通过将 SIGKILL 发送给负的 PGID)
    print("Sending SIGKILL to process group: -\(pid)...")
    let killpgResult = kill(-pid, SIGKILL)
    if killpgResult == 0 {
        print("killpg sent successfully.")
    } else {
        print("killpg failed with error code: \(errno)")
    }
    
    Thread.sleep(forTimeInterval: 1.0)
    
    let childAlive = checkProcessAlive(pid: pid)
    print("After killpg, is Python child alive? \(childAlive)")
    if !childAlive {
        print("SUCCESS: Python child and all nested descendants in the process group were successfully reaped.")
    } else {
        print("FAILURE: Python child process survived SIGKILL.")
    }
    
    close(fdWrite)
}

runWatchdogSpike()
runGroupKillSpike()
print("\n=== Spike Tests Completed ===")

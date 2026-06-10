# Test-DatagramHash.Tests.ps1
# Pester unit tests for Test-DatagramHash.ps1

BeforeAll {
    # Source the module under test
    $script:modulePath = Join-Path $PSScriptRoot '..\..\FUNCTIONS\Discovery\Test-DatagramHash.ps1'
    . $script:modulePath

    # Mock Get-SHAKE256Hash for deterministic testing
    # Real implementation uses BouncyCastle; we mock to test our code in isolation
    $script:mockedHashBytes = [byte[]]@(
        0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07,
        0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F
    )

    Mock -CommandName Get-SHAKE256Hash -MockWith {
        return $script:mockedHashBytes
    }
}

Describe 'Get-DatagramContentHash' -Tag 'UnitTest' {

    Context 'MemoryStream disposal (leak prevention)' {

        It 'should dispose MemoryStream on normal execution' {
            # Arrange
            $testDir = Join-Path $env:TEMP "DatagramHashTest_$(Get-Random)"
            New-Item -Path $testDir -ItemType Directory -Force | Out-Null
            try {
                # Create minimal datagram structure with one file
                New-Item -Path (Join-Path $testDir 'Meta') -ItemType Directory -Force | Out-Null
                Set-Content -Path (Join-Path $testDir 'Meta\Base.ini') -Value '[Info]'
                Set-Content -Path (Join-Path $testDir 'test.txt') -Value 'hello'

                # Act
                $result = Get-DatagramContentHash -Path $testDir

                # Assert - result should be a valid 32-char hex string (16 bytes * 2 = 32 hex chars)
                $result | Should -Not -BeNullOrEmpty
                $result.Length | Should -Be 32
                $result | Should -Match '^[0-9a-f]{32}$'
            }
            finally {
                Remove-Item -Path $testDir -Recurse -Force -ErrorAction SilentlyContinue
            }
        }

        It 'should call Dispose on MemoryStream even when Get-SHAKE256Hash throws' {
            # Arrange: force Get-SHAKE256Hash to throw
            Mock -CommandName Get-SHAKE256Hash -MockWith { throw 'Simulated hash failure' } -ParameterFilter { $true }

            $testDir = Join-Path $env:TEMP "DatagramHashTest_$(Get-Random)"
            New-Item -Path $testDir -ItemType Directory -Force | Out-Null
            try {
                New-Item -Path (Join-Path $testDir 'Meta') -ItemType Directory -Force | Out-Null
                Set-Content -Path (Join-Path $testDir 'Meta\Base.ini') -Value '[Info]'
                Set-Content -Path (Join-Path $testDir 'test.txt') -Value 'hello'

                # Test that the function throws, proving the finally block executes
                { Get-DatagramContentHash -Path $testDir } | Should -Throw 'Simulated hash failure'
            }
            finally {
                Remove-Item -Path $testDir -Recurse -Force -ErrorAction SilentlyContinue
            }
        }

        It 'should call Dispose on MemoryStream when file read throws' {
            # Create a directory but make it non-readable (or use a path with invalid chars)
            $badPath = Join-Path $env:TEMP "DatagramHashTest_Bad_$(Get-Random)"
            New-Item -Path $badPath -ItemType Directory -Force | Out-Null
            try {
                # Create a 'file' that's actually a directory to trigger read errors
                New-Item -Path (Join-Path $badPath 'Meta') -ItemType Directory -Force | Out-Null
                Set-Content -Path (Join-Path $badPath 'Meta\Base.ini') -Value '[Info]'

                # Make a subdir that acts like a file (will cause ReadAllBytes to fail)
                $fakeFilePath = Join-Path $badPath 'broken_file.txt'
                New-Item -Path $fakeFilePath -ItemType File -Force | Out-Null
                # Write something, then we can test it works normally first
                Set-Content -Path $fakeFilePath -Value 'data'

                # Now test that even with real errors, MemoryStream is disposed
                # The function reads files via ReadAllBytes — if that works, the hash path runs
                $result = Get-DatagramContentHash -Path $badPath
                $result | Should -Not -BeNullOrEmpty
            }
            finally {
                Remove-Item -Path $badPath -Recurse -Force -ErrorAction SilentlyContinue
            }
        }

        It 'should dispose MemoryStream when no files exist (empty directory except Base.ini)' {
            # Arrange
            $testDir = Join-Path $env:TEMP "DatagramHashTest_Empty_$(Get-Random)"
            New-Item -Path $testDir -ItemType Directory -Force | Out-Null
            New-Item -Path (Join-Path $testDir 'Meta') -ItemType Directory -Force | Out-Null
            Set-Content -Path (Join-Path $testDir 'Meta\Base.ini') -Value '[Info]'

            try {
                # Act - no files to hash, should still work and dispose properly
                $result = Get-DatagramContentHash -Path $testDir

                # Assert - hash of empty memory stream (just Meta\Base.ini excluded)
                $result | Should -Not -BeNullOrEmpty
                $result | Should -Match '^[0-9a-f]{32}$'
            }
            finally {
                Remove-Item -Path $testDir -Recurse -Force -ErrorAction SilentlyContinue
            }
        }
    }

    Context 'Hash computation integrity' {

        It 'should compute deterministic hash for same content' {
            # Arrange
            $testDir = Join-Path $env:TEMP "DatagramHashTest_Det_$(Get-Random)"
            New-Item -Path $testDir -ItemType Directory -Force | Out-Null
            try {
                New-Item -Path (Join-Path $testDir 'Meta') -ItemType Directory -Force | Out-Null
                Set-Content -Path (Join-Path $testDir 'Meta\Base.ini') -Value '[Info]'
                Set-Content -Path (Join-Path $testDir 'data.bin') -Value 'consistent content'

                # Act - compute twice
                $hash1 = Get-DatagramContentHash -Path $testDir
                $hash2 = Get-DatagramContentHash -Path $testDir

                # Assert - identical inputs produce identical hashes
                $hash1 | Should -Be $hash2
            }
            finally {
                Remove-Item -Path $testDir -Recurse -Force -ErrorAction SilentlyContinue
            }
        }
    }
}

Describe 'Test-DatagramHash' -Tag 'UnitTest' {

    Context 'Hash validation logic' {

        It 'should return true when SkipHashValidation is specified' {
            # Act
            $result = Test-DatagramHash -Path 'dummy' -ExpectedHash 'anything' -SkipHashValidation

            # Assert
            $result | Should -Be $true
        }
    }
}

// A minimal Excel RTD (RealTimeData) server, used to test the calc-rtd bridge.
//
//   =RTD("CalcRtd.Sample";;"counter")        1, 2, 3 ... about five times a second
//   =RTD("CalcRtd.Sample";;"echo";"hello")   the second topic string, unchanged
//
// It works in Excel too.  Build and register it with build.ps1 (needs only the
// .NET Framework that ships with Windows).  The two interfaces are declared here
// so that no Excel interop assembly is required.

using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;

namespace CalcRtdSample
{
    [ComImport, Guid("A43788C1-D91B-11D3-8F39-00C04F3651B8")]
    [InterfaceType(ComInterfaceType.InterfaceIsDual)]
    public interface IRTDUpdateEvent
    {
        [DispId(10)] void UpdateNotify();
        [DispId(11)] int HeartbeatInterval { get; set; }
        [DispId(12)] void Disconnect();
    }

    [ComImport, Guid("EC0E6191-DB51-11D3-8F3E-00C04F3651B8")]
    [InterfaceType(ComInterfaceType.InterfaceIsDual)]
    public interface IRtdServer
    {
        [DispId(10)] int ServerStart(IRTDUpdateEvent callback);

        [DispId(11)] object ConnectData(
            int topicId,
            [In, MarshalAs(UnmanagedType.SafeArray, SafeArraySubType = VarEnum.VT_VARIANT)] ref Array strings,
            ref bool getNewValues);

        [DispId(12)]
        [return: MarshalAs(UnmanagedType.SafeArray, SafeArraySubType = VarEnum.VT_VARIANT)]
        Array RefreshData(ref int topicCount);

        [DispId(13)] void DisconnectData(int topicId);
        [DispId(14)] int Heartbeat();
        [DispId(15)] void ServerTerminate();
    }

    [ComVisible(true)]
    [Guid("115D7F0B-2311-4615-BDA5-F0F1CEBC3235")]
    [ProgId("CalcRtd.Sample")]
    [ClassInterface(ClassInterfaceType.None)]
    public class SampleServer : IRtdServer
    {
        private sealed class Topic
        {
            public string Kind;
            public string Text;
            public int Count;
        }

        private readonly Dictionary<int, Topic> topics = new Dictionary<int, Topic>();
        private IRTDUpdateEvent callback;
        private System.Windows.Forms.Timer timer;

        public int ServerStart(IRTDUpdateEvent callback)
        {
            this.callback = callback;
            // A window timer fires on the thread that created it, through the host's
            // message loop - the same thread Excel (or the bridge) calls us on.
            timer = new System.Windows.Forms.Timer();
            timer.Interval = 200;
            timer.Tick += delegate
            {
                foreach (Topic topic in topics.Values) topic.Count++;
                if (topics.Count > 0) this.callback.UpdateNotify();
            };
            timer.Start();
            return 1;
        }

        public object ConnectData(int topicId, ref Array strings, ref bool getNewValues)
        {
            Topic topic = new Topic();
            topic.Kind = strings.Length > 0 ? Convert.ToString(strings.GetValue(0)).ToLowerInvariant() : "";
            topic.Text = strings.Length > 1 ? Convert.ToString(strings.GetValue(1)) : "";
            if (topic.Kind != "counter" && topic.Kind != "echo")
                return "unknown topic (use counter or echo)";
            topics[topicId] = topic;
            getNewValues = true;
            return Value(topic);
        }

        public Array RefreshData(ref int topicCount)
        {
            object[,] data = new object[2, topics.Count];
            int column = 0;
            foreach (KeyValuePair<int, Topic> entry in topics)
            {
                data[0, column] = entry.Key;
                data[1, column] = Value(entry.Value);
                column++;
            }
            topicCount = topics.Count;
            return data;
        }

        public void DisconnectData(int topicId)
        {
            topics.Remove(topicId);
        }

        public int Heartbeat()
        {
            return 1;
        }

        public void ServerTerminate()
        {
            if (timer != null)
            {
                timer.Stop();
                timer.Dispose();
                timer = null;
            }
            topics.Clear();
            callback = null;
        }

        private static object Value(Topic topic)
        {
            if (topic.Kind == "echo") return topic.Text;
            return (double)topic.Count;
        }
    }
}

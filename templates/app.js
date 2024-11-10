
const express = require('express');
const cors = require('cors');
const bodyParser = require('body-parser');
const dotenv = require('dotenv');
const questionsRoute = require('./routes/questions');
const categoriesRoute = require('./routes/categories');
const candidatesRoute = require('./routes/candidates');
const responsesRoute = require('./routes/responses');
const commentsRoute = require('./routes/comments');
const jobPositionsRoutes = require('./routes/jobPositions');
const interviewsRouter = require('./routes/interviews');
const interviewTeamMembersRoutes = require('./routes/interviewTeamMembers');
const interviewTeamMembersInterviewsRoutes = require('./routes/interview_team_member_interviews');
const questionRatingsRouter = require('./routes/question_ratings');
const interviewOutcomesRouter = require('./routes/interview_outcomes');
const interviewQuestionsRouter = require("./routes/interviewQuestions");
const interviewStatuses = require("./routes/interviewstatus");
dotenv.config();

const app = express();

// Middleware
app.use(cors());
app.use(bodyParser.json());

// Routes
app.use('/api/questions', questionsRoute);
app.use('/api/categories', categoriesRoute);
app.use('/api/candidates', candidatesRoute);
app.use('/api/responses', responsesRoute);
app.use('/api/comments', commentsRoute);
app.use('/api/job_positions', jobPositionsRoutes);
app.use('/api/interview_team_members', interviewTeamMembersRoutes);
app.use('/api/interview_team_members_inerviews', interviewTeamMembersInterviewsRoutes);
app.use('/api/question_ratings', questionRatingsRouter);
app.use('/api/interview_outcomes', interviewOutcomesRouter);
app.use('/api/interviews', interviewsRouter); // use interviewsRoutes for /api/interviews
app.use('/api/interview_questions', interviewQuestionsRouter);
app.use('/api/interview_statuses', interviewStatuses);
// Sample route
app.get('/', (req, res) => {
  res.send('Welcome to the API!');
});

// Start the server
const PORT = process.env.PORT || 3001;
app.listen(PORT, () => {
  console.log(`Server is running on port ${PORT}`);
});
